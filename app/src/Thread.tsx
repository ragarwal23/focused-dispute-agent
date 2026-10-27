// The chat column: the customer's message, the assistant's steps as they happen, the pause where the rep decides,
// and the filed case. Every step carries who did it: AI (an LLM call), code (plain rules), or you.
import { useEffect, useState } from "react";
import { api, ROUTES, ROUTE_LABEL, type Decision, type Owner, type ResultEvent, type ReviewRequest, type Route } from "./api";
import type { Step, ThreadState } from "./App";

const OWNER: Record<Owner, string> = { ai: "AI", code: "code", you: "you" };

function Badge({ owner }: { owner: Owner }) {
  return <span className={`badge ${owner}`}>{OWNER[owner]}</span>;
}

function Ids({ ids }: { ids: string[] }) {
  return <>{ids.length ? ids.map((id) => <code key={id} className="chip">{id}</code>) : <span className="muted">none</span>}</>;
}

function Trail({ steps, streaming, collapsed }: { steps: Step[]; streaming: boolean; collapsed: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  useEffect(() => setOpen(!collapsed), [collapsed]);
  const shown = steps.filter((s) => s.status !== "done" || s.label);
  const done = shown.filter((s) => s.status !== "running");
  const running = shown.filter((s) => s.status === "running");
  if (!shown.length) return streaming ? <p className="trail-wait">Starting<span className="dots" /></p> : null;
  return (
    <div className="trail">
      {done.length > 0 && (
        <button className="trail-toggle" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} {done.length} steps</button>
      )}
      <ol>
        {(open ? done : []).concat(running).map((s) => (
          <li key={s.id} className={`step ${s.status} ${s.parent ? "sub" : ""}`}>
            <Badge owner={s.owner} />
            <span className="step-text">
              {s.label}{s.status === "running" && <span className="dots" />}
              {s.status === "stopped" && <em className="muted"> (stopped)</em>}
              {s.detail && <span className="step-detail">{s.detail}</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function PauseCard({ req, onDecide }: { req: ReviewRequest; onDecide: (d: Decision) => void }) {
  const [mode, setMode] = useState<"none" | "override" | "answer">("none");
  const [route, setRoute] = useState<Route>(ROUTES.find((r) => r !== req.route) ?? "manual_escalation");
  const [reason, setReason] = useState("");
  const [answer, setAnswer] = useState("");
  const d = req.draft;
  const again = [req.account_attempts > 1 && "the account was read twice", req.policy_attempts > 1 && "the policy was re-read",
    req.followups > 0 && "the account was read again with your answer"].filter(Boolean);
  return (
    <section className="card pause">
      <header className="card-head">
        <Badge owner="you" /><h3>Your decision</h3>
        {req.gate_rejected ? <span className="flag warn">Gate overrode the draft</span> : <span className="flag ok">Gate agrees</span>}
      </header>
      <div className="route-line">
        <span className="muted">Recommended route</span>
        {req.gate_rejected && d && d.review_route !== req.route && <s className="route old">{ROUTE_LABEL[d.review_route]}</s>}
        <strong className="route">{ROUTE_LABEL[req.route]}</strong>
        {d && <span className={`confidence ${d.confidence}`}>{d.confidence} confidence</span>}
      </div>
      {!d && <p className="alert">The assistant could not write a draft. The gate sent it to manual escalation.</p>}
      {req.gate_rejected && <ul className="reasons">{req.gate_reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      {req.existing_case_id && (
        <p className="alert">An open case already exists: <code>{req.existing_case_id}</code>. Approving files nothing new.</p>
      )}
      {d && (
        <div className="draft">
          <p className="note">{d.case_note}</p>
          <dl>
            <dt>Records cited</dt><dd><Ids ids={d.event_ids} /></dd>
            <dt>Policy cited</dt><dd><Ids ids={d.policy_source_ids} /></dd>
          </dl>
        </div>
      )}
      {req.open_questions.length > 0 && (
        <div className="questions"><h4>Still unknown</h4><ul>{req.open_questions.map((q) => <li key={q}>{q}</li>)}</ul></div>
      )}
      {again.length > 0 && <p className="muted small">Before this pause, {again.join("; ")}.</p>}

      <div className="actions">
        <button className="primary" onClick={() => onDecide({ action: "approve" })}>Approve · file as {ROUTE_LABEL[req.route]}</button>
        <button className={mode === "override" ? "ghost on" : "ghost"} onClick={() => setMode(mode === "override" ? "none" : "override")}>Override</button>
        {req.can_answer && (
          <button className={mode === "answer" ? "ghost on" : "ghost"} onClick={() => setMode(mode === "answer" ? "none" : "answer")}>Answer</button>
        )}
      </div>
      {mode === "override" && (
        <div className="subform">
          <label>File under
            <select value={route} onChange={(e) => setRoute(e.target.value as Route)}>
              {ROUTES.map((r) => <option key={r} value={r}>{ROUTE_LABEL[r]}</option>)}
            </select>
          </label>
          <label>Reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why the route changes" /></label>
          <button className="primary" disabled={!reason.trim()} onClick={() => onDecide({ action: "override", route, reason: reason.trim() })}>File it</button>
        </div>
      )}
      {mode === "answer" && req.can_answer && (
        <div className="subform">
          <label>Your answer (once; the assistant looks at the account again)
            <textarea rows={3} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="e.g. the customer means the $731.50 refund requested Oct 6" />
          </label>
          <button className="primary" disabled={!answer.trim()} onClick={() => onDecide({ action: "answer", answer: answer.trim() })}>Send answer</button>
        </div>
      )}
    </section>
  );
}

function ResultCard({ result, threadId }: { result: ResultEvent; threadId: string | null }) {
  const [trace, setTrace] = useState<string | null>(null);
  const c = result.case, r = result.review;
  const who = r.action === "override" ? `you, overriding to ${ROUTE_LABEL[c.route]}: “${r.reason}”` : "you, approving the recommended route";
  async function viewTrace() {
    if (!threadId) return;
    try {
      const url = await api.trace(threadId);
      window.open(url, "_blank", "noopener");
      setTrace(url);
    } catch (e) {
      setTrace(`unavailable: ${(e as Error).message}`);
    }
  }
  return (
    <section className="card result">
      <header className="card-head">
        <Badge owner="code" /><h3>{c.created ? "Case filed" : "Existing case kept"}</h3>
        <code className="case-id">{c.case_id}</code>
      </header>
      <dl>
        <dt>Queue</dt><dd><strong>{ROUTE_LABEL[c.route]}</strong></dd>
        <dt>Decided by</dt><dd>{who}</dd>
        <dt>Disputed record</dt><dd>{c.disputed_event_id ? <code className="chip">{c.disputed_event_id}</code> : <span className="muted">none identified</span>}</dd>
        <dt>Created</dt><dd>{c.created ? "new case" : "no: an open case already covered this"}</dd>
      </dl>
      <div className="actions">
        <button className="ghost" onClick={viewTrace}>View trace in LangSmith</button>
        {trace && (trace.startsWith("http") ? <a href={trace} target="_blank" rel="noreferrer">open trace ↗</a> : <span className="muted small">trace {trace}</span>)}
      </div>
    </section>
  );
}

export default function Thread({ thread: t, onDecide }: { thread: ThreadState; onDecide: (d: Decision) => void }) {
  return (
    <article className="thread">
      <header className="thread-head">
        <div><h2>{t.customer}</h2><p className="muted">{t.channel}</p></div>
        <div className="ids"><code>{t.accountId}</code>{t.threadId && <code className="muted">{t.threadId}</code>}</div>
      </header>
      <div className="bubble"><span className="bubble-who">Customer</span>{t.customerMessage}</div>
      <Trail steps={t.steps} streaming={t.streaming} collapsed={!t.streaming && (t.pause !== null || t.result !== null)} />
      {t.error && (
        <section className="card error">
          <p><strong>{t.pause ? "Not sent." : "The run stopped."}</strong> {t.error}</p>
          {t.threadId && !t.pause && !t.result && (
            <button className="primary" onClick={() => onDecide({ action: "approve" })}>Continue from checkpoint</button>
          )}
        </section>
      )}
      {t.pause && !t.streaming && <PauseCard key={`${t.threadId}-${t.pause.followups}`} req={t.pause} onDecide={onDecide} />}
      {t.result && <ResultCard result={t.result} threadId={t.threadId} />}
    </article>
  );
}
