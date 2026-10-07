"use client";

import { useEffect, useRef, useState } from "react";
import { readAskStream, type AskEvent, type Source } from "@/lib/ask";

type Identity = { principal_id: string; tenant_id: string };
type Evidence = {
  chunk_id: string; source_id: string; document_id: string; document_version_id: string;
  text: string; span: [number, number]; is_current: boolean; source_timestamp: string;
};
type FinalEvent = Extract<AskEvent, { type: "final" }>;
type InvestigationStatus = {
  investigation_id: string; status: string; updated_at: string; stop_reason: string | null;
  report_available: boolean;
  progress: { steps: number; tokens_used: number; tool_calls: number; retrieval_attempts: number };
};

const apiError = (status: number) => status === 401 ? "Session expired. Add a valid JWT to continue." : "The API is unavailable or could not complete the request.";

export function Workspace() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [sessionState, setSessionState] = useState<"checking" | "signed_out" | "ready">("checking");
  const [token, setToken] = useState("");
  const [sessionError, setSessionError] = useState("");
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [stage, setStage] = useState("");
  const [draft, setDraft] = useState("");
  const [answer, setAnswer] = useState<FinalEvent | null>(null);
  const [answerId, setAnswerId] = useState("");
  const [askError, setAskError] = useState("");
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [evidenceError, setEvidenceError] = useState("");
  const [feedbackState, setFeedbackState] = useState("");
  const [investigationId, setInvestigationId] = useState("");
  const [investigation, setInvestigation] = useState<InvestigationStatus | null>(null);
  const [investigationError, setInvestigationError] = useState("");
  const [investigationPending, setInvestigationPending] = useState(false);
  const sourceRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    fetch("/api/session", { cache: "no-store" }).then(async (response) => {
      if (response.ok) {
        setIdentity(await response.json() as Identity);
        setSessionState("ready");
      } else {
        setSessionState("signed_out");
      }
    }).catch(() => setSessionState("signed_out"));
  }, []);

  async function signIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSessionError("");
    try {
      const response = await fetch("/api/session", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
      });
      if (!response.ok) throw new Error(apiError(response.status));
      setIdentity(await response.json() as Identity);
      setToken("");
      setSessionState("ready");
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Could not start the session.");
    }
  }

  async function signOut() {
    await fetch("/api/session", { method: "DELETE" });
    setIdentity(null);
    setSessionState("signed_out");
    setAnswer(null);
    setEvidence([]);
  }

  async function searchEvidence(query: string) {
    setEvidenceError("");
    try {
      const response = await fetch(`/api/evidence?q=${encodeURIComponent(query)}`, { cache: "no-store" });
      if (response.status === 401) { setIdentity(null); setSessionState("signed_out"); }
      if (!response.ok) throw new Error(apiError(response.status));
      const result = await response.json() as { evidence: Evidence[] };
      setEvidence(result.evidence);
    } catch (error) {
      setEvidenceError(error instanceof Error ? error.message : "Evidence could not be loaded.");
    }
  }

  async function ask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim() || pending) return;
    setPending(true);
    setAnswer(null);
    setDraft("");
    setAskError("");
    setFeedbackState("");
    setStage("Retrieving evidence");
    setEvidence([]);
    const submittedQuestion = question.trim();
    try {
      const response = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: submittedQuestion }),
      });
      if (response.status === 401) { setIdentity(null); setSessionState("signed_out"); }
      if (!response.ok || !response.body) throw new Error(apiError(response.status));
      for await (const item of readAskStream(response.body)) {
        if (item.type === "status") setStage(item.status === "generating" ? "Generating and checking citations" : "Retrieving evidence");
        if (item.type === "delta") setDraft((current) => current + item.text);
        if (item.type === "final") {
          setAnswer(item);
          setDraft("");
          setStage("");
          if (item.status === "verified") {
            setAnswerId(item.answer_id ?? "");
            void searchEvidence(submittedQuestion);
          }
        }
      }
    } catch (error) {
      setDraft("");
      setAskError(error instanceof Error ? error.message : "The answer stream failed.");
    } finally {
      setPending(false);
    }
  }

  async function sendFeedback(rating: "helpful" | "not_helpful") {
    setFeedbackState("Sending feedback…");
    try {
      const response = await fetch("/api/feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer_id: answerId, rating }),
      });
      if (response.status === 401) { setIdentity(null); setSessionState("signed_out"); }
      if (!response.ok) throw new Error(apiError(response.status));
      setFeedbackState("Feedback recorded. Thank you.");
    } catch (error) {
      setFeedbackState(error instanceof Error ? error.message : "Feedback was not recorded.");
    }
  }

  async function checkInvestigation(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!investigationId.trim()) return;
    setInvestigationPending(true);
    setInvestigationError("");
    setInvestigation(null);
    try {
      const response = await fetch(`/api/investigations/${encodeURIComponent(investigationId.trim())}`, { cache: "no-store" });
      if (response.status === 401) { setIdentity(null); setSessionState("signed_out"); }
      if (response.status === 404) throw new Error("Investigation not found or not enabled in this runtime.");
      if (!response.ok) throw new Error(apiError(response.status));
      setInvestigation(await response.json() as InvestigationStatus);
    } catch (error) {
      setInvestigationError(error instanceof Error ? error.message : "Status could not be loaded.");
    } finally {
      setInvestigationPending(false);
    }
  }

  function jumpToSource(source: Source) {
    sourceRefs.current[source.evidence_id]?.scrollIntoView({ behavior: "smooth", block: "center" });
    sourceRefs.current[source.evidence_id]?.focus();
  }

  if (sessionState === "checking") return <main className="centered" aria-live="polite">Checking session…</main>;

  if (sessionState === "signed_out") return (
    <main className="signin-layout">
      <div className="brand">G<span>O</span><span className="brand-name">GroundedOps</span></div>
      <section className="signin-card" aria-labelledby="signin-title">
        <p className="eyebrow">LOCAL DEMONSTRATION</p>
        <h1 id="signin-title">Evidence before answers.</h1>
        <p>Connect with a valid API JWT. Your browser sends it once to the web server, which stores it in an HttpOnly cookie. The API still authorizes every request.</p>
        <form onSubmit={signIn}>
          <label htmlFor="jwt">API access token</label>
          <input id="jwt" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} required />
          <button type="submit">Start local session <span aria-hidden="true">→</span></button>
        </form>
        {sessionError && <p className="error" role="alert">{sessionError}</p>}
        <p className="fine-print">No identity-provider login is configured for this local preview.</p>
      </section>
    </main>
  );

  const citedSources = answer?.sources ?? [];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">G<span>O</span><span className="brand-name">GroundedOps</span></div>
        <nav aria-label="Workspace"><a href="#ask" aria-current="page">Ask workspace</a><a href="#evidence">Evidence</a><a href="#investigation">Investigation</a></nav>
        <div className="sidebar-bottom"><span className="online-dot" /> API-authorized session<br /><small>{identity?.principal_id} · {identity?.tenant_id}</small><button className="text-button" onClick={signOut}>Sign out</button></div>
      </aside>
      <main className="main-content" id="ask">
        <header className="topbar"><span>ENGINEERING INTELLIGENCE</span><span className="environment">LOCAL PREVIEW</span></header>
        <div className="content-grid">
          <section className="primary-column">
            <div className="intro"><p className="eyebrow">ASK / VERIFIED ANSWERS</p><h1>Ask with confidence.<br /><em>Verify with evidence.</em></h1><p>Trace operational guidance back to its source. When evidence is weak, GroundedOps says so.</p></div>
            <form className="ask-form" onSubmit={ask}>
              <label htmlFor="question">Your question</label>
              <textarea id="question" maxLength={1000} rows={4} placeholder="What is the current recovery procedure for this service?" value={question} onChange={(event) => setQuestion(event.target.value)} />
              <div className="form-footer"><span>{question.length}/1000 characters</span><button disabled={pending || !question.trim()} type="submit">{pending ? "Working…" : "Ask GroundedOps"} <span aria-hidden="true">↗</span></button></div>
            </form>
            <section className="answer-panel" aria-labelledby="answer-heading">
              <div className="panel-heading"><div><p className="eyebrow">RESPONSE</p><h2 id="answer-heading">Grounded answer</h2></div>{answer && <span className={`badge ${answer.status}`}>{answer.status}</span>}</div>
              {!answer && !pending && !askError && <div className="empty-state"><span className="empty-icon" aria-hidden="true">✳</span><h3>Answers start with evidence.</h3><p>Ask a question to inspect the answer and its cited document versions here.</p></div>}
              {pending && <div role="status" className="working"><span className="spinner" />{stage}{draft && <div className="draft"><strong>Unverified draft — not an answer</strong><p>{draft}</p></div>}</div>}
              {askError && <p className="error" role="alert">{askError}</p>}
              {answer?.status === "verified" && <div className="verified-answer"><p className="answer-copy">{answer.answer}</p><div className="claims"><h3>Claims & citations</h3>{answer.claims.map((claim, index) => <div className="claim" key={`${claim.text}-${index}`}><p>{claim.text}</p><div className="citation-list">{claim.citations.map((citation) => {
                const source = citedSources.find((item) => item.evidence_id === citation.evidence_id);
                return <button key={`${citation.evidence_id}-${citation.span.join("-")}`} type="button" className="citation" onClick={() => source && jumpToSource(source)} disabled={!source}>↗ {citation.evidence_id.slice(0, 12)} · v{citation.document_version_id.slice(0, 8)} · {citation.span.join("–")}</button>;
              })}</div></div>)}</div><div className="feedback"><span>Was this answer useful?</span><button onClick={() => sendFeedback("helpful")} type="button">Yes</button><button onClick={() => sendFeedback("not_helpful")} type="button">No</button><p role="status">{feedbackState}</p></div></div>}
              {answer?.status === "abstained" && <div className="notice"><h3>Not enough evidence</h3><p>{answer.answer}</p><small>Reason: {answer.abstention ?? "insufficient evidence"}</small></div>}
              {answer?.status === "degraded" && <div className="notice"><h3>Evidence-only mode</h3><p>Generation is unavailable. No answer was produced; inspect the source references below.</p></div>}
              {answer?.status === "failed" && <div className="notice error"><h3>Could not answer</h3><p>{answer.error?.message ?? "Try again later."}</p></div>}
            </section>
          </section>
          <aside className="right-column">
            <section className="evidence-panel" id="evidence" aria-labelledby="evidence-heading"><div className="panel-heading"><div><p className="eyebrow">SOURCE TRACE</p><h2 id="evidence-heading">Evidence</h2></div><span className="count">{citedSources.length}</span></div>
              {citedSources.length === 0 && <p className="muted">Cited sources will appear after a verified answer.</p>}
              {citedSources.map((source, index) => {
                const item = evidence.find((candidate) => candidate.chunk_id === source.evidence_id && candidate.document_version_id === source.document_version_id);
                return <article key={`${source.evidence_id}-${index}`} className="source-card" id={`source-${index}`} tabIndex={-1} ref={(node) => { sourceRefs.current[source.evidence_id] = node; }}><div className="source-top"><span>SOURCE {String(index + 1).padStart(2, "0")}</span><span className={item?.is_current === false ? "historical" : "current"}>{item ? item.is_current ? "CURRENT" : "HISTORICAL" : "REFERENCE"}</span></div><h3>{source.document_id}</h3><p className="source-meta">{source.source_id} · version {source.document_version_id}</p><p className="source-meta">Span {source.span.join("–")}</p>{item ? <blockquote>{item.text}</blockquote> : <p className="muted">Source text is not available in the current authorized search results. Reference metadata is shown without inventing an excerpt.</p>}</article>;
              })}
              {evidenceError && <p className="error" role="alert">{evidenceError}</p>}
            </section>
            <section className="investigation-panel" id="investigation" aria-labelledby="investigation-heading">
              <p className="eyebrow">BOUNDED WORKFLOW</p><h2 id="investigation-heading">Investigation</h2>
              <span className="badge unavailable">NEW RUNS DISABLED</span>
              <p>The agentic workflow remains gated by quality and cost thresholds. Status lookup is available for runtimes that expose investigations.</p>
              <form className="status-form" onSubmit={checkInvestigation}>
                <label htmlFor="investigation-id">Investigation ID</label>
                <input id="investigation-id" value={investigationId} maxLength={128} onChange={(event) => setInvestigationId(event.target.value)} placeholder="inv-…" />
                <button type="submit" disabled={investigationPending || !investigationId.trim()}>{investigationPending ? "Checking…" : "Check status"}</button>
              </form>
              {investigationError && <p className="error" role="alert">{investigationError}</p>}
              {investigation && <div className="status-result" role="status"><strong>{investigation.status}</strong><span>{investigation.investigation_id}</span><span>Steps {investigation.progress.steps} · tools {investigation.progress.tool_calls} · tokens {investigation.progress.tokens_used}</span><span>Updated {new Date(investigation.updated_at).toLocaleString()}</span>{investigation.stop_reason && <span>Stop reason: {investigation.stop_reason}</span>}</div>}
            </section>
          </aside>
        </div>
      </main>
    </div>
  );
}
