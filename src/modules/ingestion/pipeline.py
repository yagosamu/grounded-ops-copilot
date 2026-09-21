"""Coordinate durable ingestion up to the rebuildable index boundary."""

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Protocol

from domain.ingestion import Document, DocumentVersion, IngestionJob, JobState, Source
from modules.audit.recorder import AuditOutcome, AuditRecorder
from modules.chunking.structural import Chunk, ProvenanceSpan, StructuralChunker
from modules.ingestion.errors import ArtifactFailure
from modules.parsing.parser import MarkdownParser, ParserInputError
from observability.telemetry import (
    EntryPoint,
    Telemetry,
    TelemetryComponent,
    TelemetryOperation,
    TelemetryOutcome,
)


class PipelineInputError(ValueError):
    """A content-free error for source events that cannot enter the pipeline."""


class IndexPreparationError(RuntimeError):
    """A safe retryable failure at the future search projection boundary."""


class SourceEventView(Protocol):
    kind: str
    source: Source
    canonical_key: str
    source_version: str
    source_timestamp: datetime
    content: bytes | None


class ArtifactView(Protocol):
    @property
    def key(self) -> str: ...


class ArtifactStore(Protocol):
    def put(self, tenant_id: str, kind: str, content: bytes) -> ArtifactView: ...


class SubmissionView(Protocol):
    document: Document
    version: DocumentVersion
    job: IngestionJob


class MetadataRepository(Protocol):
    def submit(
        self,
        source: Source,
        canonical_key: str,
        source_version: str,
        content_hash: str,
        source_timestamp: datetime,
        parser_version: str,
    ) -> SubmissionView: ...

    def get_job(self, tenant_id: str, job_id: str) -> IngestionJob | None: ...

    def save_job(self, job: IngestionJob) -> IngestionJob: ...

    def update_artifacts(
        self,
        tenant_id: str,
        version_id: str,
        raw_ref: str,
        normalized_ref: str,
    ) -> DocumentVersion: ...

    def promote(self, tenant_id: str, version_id: str) -> None: ...

    def get_document_by_key(
        self, tenant_id: str, source_id: str, canonical_key: str
    ) -> Document | None: ...

    def delete(self, tenant_id: str, document_id: str) -> None: ...


@dataclass(frozen=True)
class PreparedChunk:
    id: str
    tenant_id: str
    source_id: str
    document_id: str
    document_version_id: str
    ordinal: int
    text: str
    start: int
    end: int
    content_hash: str
    spans: tuple[ProvenanceSpan, ...]
    parser_version: str
    chunker_version: str


class IndexPreparationSink(Protocol):
    def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None: ...


@dataclass(frozen=True)
class DeadLetter:
    job_id: str
    retry_key: str
    error_class: str
    attempts: int


@dataclass(frozen=True)
class PipelineOutcome:
    kind: str
    job: IngestionJob | None
    version_id: str | None
    document_id: str | None
    prepared_chunks: tuple[PreparedChunk, ...] = ()
    dlq: DeadLetter | None = None


class IngestionPipeline:
    def __init__(
        self,
        repository: MetadataRepository,
        store: ArtifactStore,
        parser: MarkdownParser,
        chunker: StructuralChunker,
        index_sink: IndexPreparationSink,
        max_attempts: int,
        audit: AuditRecorder | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.repository = repository
        self.store = store
        self.parser = parser
        self.chunker = chunker
        self.index_sink = index_sink
        self.max_attempts = max_attempts
        self.audit = audit or AuditRecorder.ephemeral()
        self.telemetry = telemetry or Telemetry()

    def run(self, event: SourceEventView) -> PipelineOutcome:
        with self.telemetry.operation(
            TelemetryComponent.WORKER,
            TelemetryOperation.INGESTION_RUN,
            default_entry_point=EntryPoint.WORKER,
        ) as observation:
            outcome = self._run(event)
            observation.outcome = _pipeline_telemetry_outcome(outcome)
            return outcome

    def _run(self, event: SourceEventView) -> PipelineOutcome:
        if event.kind == "deleted":
            document = self.repository.get_document_by_key(
                event.source.tenant_id, event.source.id, event.canonical_key
            )
            if document is not None:
                self.repository.delete(event.source.tenant_id, document.id)
            self.audit.record_ingestion(
                tenant_id=event.source.tenant_id,
                job_id=document.id if document else event.canonical_key,
                outcome=AuditOutcome.DELETED,
            )
            return PipelineOutcome(
                "deleted", None, None, document.id if document else None
            )
        content = self._content(event)
        submission = self.repository.submit(
            event.source,
            event.canonical_key,
            event.source_version,
            sha256(content).hexdigest(),
            event.source_timestamp,
            self.parser.VERSION,
        )
        return self._continue(event, submission, submission.job)

    def resume(
        self, tenant_id: str, job_id: str, event: SourceEventView
    ) -> PipelineOutcome:
        with self.telemetry.operation(
            TelemetryComponent.WORKER,
            TelemetryOperation.INGESTION_RESUME,
            default_entry_point=EntryPoint.WORKER,
        ) as observation:
            outcome = self._resume(tenant_id, job_id, event)
            observation.outcome = _pipeline_telemetry_outcome(outcome)
            return outcome

    def _resume(
        self, tenant_id: str, job_id: str, event: SourceEventView
    ) -> PipelineOutcome:
        job = self.repository.get_job(tenant_id, job_id)
        if job is None or event.source.tenant_id != tenant_id:
            raise PipelineInputError("invalid ingestion resume")
        content = self._content(event)
        submission = self.repository.submit(
            event.source,
            event.canonical_key,
            event.source_version,
            sha256(content).hexdigest(),
            event.source_timestamp,
            self.parser.VERSION,
        )
        if submission.job.id != job_id:
            raise PipelineInputError("invalid ingestion resume")
        return self._continue(event, submission, job)

    def _continue(
        self,
        event: SourceEventView,
        submission: SubmissionView,
        job: IngestionJob,
    ) -> PipelineOutcome:
        if job.state in {JobState.COMPLETED, JobState.FAILED}:
            return self._outcome(submission, job, ())
        if job.state == JobState.QUEUED:
            job = self.repository.save_job(job.transition(JobState.FETCHING))
        elif job.state == JobState.RETRYING:
            if job.resume_from is None:
                raise PipelineInputError("invalid ingestion checkpoint")
            job = self.repository.save_job(
                replace(job.transition(job.resume_from), error_class=None)
            )

        content = self._content(event)
        prepared: tuple[PreparedChunk, ...] = ()
        raw: ArtifactView | None = None
        try:
            if job.state == JobState.FETCHING:
                raw = self.store.put(event.source.tenant_id, "raw", content)
                job = self.repository.save_job(job.transition(JobState.PARSING))
            if job.state == JobState.PARSING:
                parsed = self.parser.parse(content)
                if raw is None:
                    raw = self.store.put(event.source.tenant_id, "raw", content)
                normalized = self.store.put(
                    event.source.tenant_id,
                    "normalized",
                    parsed.normalized_text.encode("utf-8"),
                )
                self.repository.update_artifacts(
                    event.source.tenant_id,
                    submission.version.id,
                    raw.key,
                    normalized.key,
                )
                job = self.repository.save_job(job.transition(JobState.INDEXING))
            if job.state == JobState.INDEXING:
                parsed = self.parser.parse(content)
                chunks = self.chunker.chunk(parsed)
                prepared = self._prepare(event, submission, chunks)
                self.index_sink.prepare(prepared)
                self.repository.promote(event.source.tenant_id, submission.version.id)
                self.audit.record_index_promotion(
                    tenant_id=event.source.tenant_id,
                    version_id=submission.version.id,
                    promoted=True,
                    correlation_id=job.id,
                )
                job = self.repository.save_job(job.transition(JobState.COMPLETED))
            return self._outcome(submission, job, prepared)
        except (ArtifactFailure, ParserInputError, IndexPreparationError) as error:
            return self._failed_attempt(submission, job, error)

    def _failed_attempt(
        self,
        submission: SubmissionView,
        job: IngestionJob,
        error: ArtifactFailure | ParserInputError | IndexPreparationError,
    ) -> PipelineOutcome:
        resume_from = job.state
        if isinstance(error, ArtifactFailure):
            error_class = "artifact"
        elif isinstance(error, ParserInputError):
            error_class = "parser_input"
        else:
            error_class = "index_preparation"
        job = replace(
            job.transition(JobState.RETRYING),
            attempts=job.attempts + 1,
            error_class=error_class,
            resume_from=resume_from,
        )
        if job.attempts >= self.max_attempts:
            job = job.transition(JobState.FAILED)
        job = self.repository.save_job(job)
        return self._outcome(submission, job, ())

    def _prepare(
        self,
        event: SourceEventView,
        submission: SubmissionView,
        chunks: tuple[Chunk, ...],
    ) -> tuple[PreparedChunk, ...]:
        return tuple(
            PreparedChunk(
                sha256(f"{submission.version.id}:{chunk.ordinal}".encode()).hexdigest(),
                event.source.tenant_id,
                event.source.id,
                submission.document.id,
                submission.version.id,
                chunk.ordinal,
                chunk.text,
                chunk.start,
                chunk.end,
                sha256(chunk.text.encode()).hexdigest(),
                chunk.spans,
                chunk.parser_version,
                chunk.chunker_version,
            )
            for chunk in chunks
        )

    @staticmethod
    def _content(event: SourceEventView) -> bytes:
        if event.kind == "malformed" or event.content is None:
            raise PipelineInputError("invalid source event")
        return event.content

    def _outcome(
        self,
        submission: SubmissionView,
        job: IngestionJob,
        prepared: tuple[PreparedChunk, ...],
    ) -> PipelineOutcome:
        outcomes = {
            JobState.RETRYING: AuditOutcome.RETRYING,
            JobState.COMPLETED: AuditOutcome.COMPLETED,
            JobState.FAILED: AuditOutcome.FAILED,
        }
        if audit_outcome := outcomes.get(job.state):
            self.audit.record_ingestion(
                tenant_id=job.tenant_id,
                job_id=job.id,
                outcome=audit_outcome,
                correlation_id=job.id,
            )
        dlq = (
            DeadLetter(
                job.id, job.idempotency_key, job.error_class or "unknown", job.attempts
            )
            if job.state == JobState.FAILED
            else None
        )
        return PipelineOutcome(
            job.state.value,
            job,
            submission.version.id,
            submission.document.id,
            prepared,
            dlq,
        )


def _pipeline_telemetry_outcome(outcome: PipelineOutcome) -> TelemetryOutcome:
    if outcome.kind == JobState.FAILED.value:
        return TelemetryOutcome.ERROR
    if outcome.kind == JobState.RETRYING.value:
        return TelemetryOutcome.PARTIAL
    return TelemetryOutcome.SUCCESS
