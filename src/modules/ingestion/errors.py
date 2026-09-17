"""Retryable ingestion boundary failures."""


class ArtifactFailure(ValueError):
    """A redacted failure while reading or writing an immutable artifact."""
