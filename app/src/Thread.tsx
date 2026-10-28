// The conversation: the rep's messages on the right, the assistant's replies on the left, the composer at the bottom.
// An assistant reply shows a phase bar and its steps inline as it works, then its recommendation and the rep's choices.
import { useEffect, useRef, useState } from "react";
import { api, ROUTES, ROUTE_LABEL, type Decision, type ReviewRequest, type ResultEvent, type Route, type Source, type Sources } from "./api";
import { activePause, type AssistantTurn, type Step, type ThreadState, type Turn } from "./App";

const PHASES = ["Reading", "Investigating", "Drafting"];
const PHASE_OF: Record<string, number> = { classify: 0, synthesize: 2, review: 3, create_review_case: 3 };

/** Where the assistant is: the phase of its latest step, or 3 (all done) once it has answered. */
function phaseOf(t: AssistantTurn): number {
  if (t.pause || t.result) return 3;
  const last = t.steps.filter((s) => !s.parent).at(-1);
  return last ? PHASE_OF[last.key] ?? 1 : 0;
}
const category = (steps: Step[]) => steps.find((s) => s.key === "classify" && s.status === "done")?.label.replace("Dispute type: ", "");

const money = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

function SourceCard({ s }: { s: Source }) {
  if (s.kind === "policy") return (
    <><strong>{s.title}</strong><span>{s.doc} · in force from {day(s.effective_from)}{s.status === "superseded" && " · superseded"}</span></>
  );
  return (
    <><strong>{s.type.replace(/_/g, " ")}</strong>
      <span>{[day(s.date), s.amount != null && money(s.amount), s.channel].filter(Boolean).join(" · ")}</span>
      {s.memo && <blockquote className="memo"><span className="eyebrow">Memo, as recorded</span>{s.memo}</blockquote>}</>
  );
}

/** A cited ID; hover or focus it to see what the record or policy section is. */
export function Chip({ id, sources }: { id: string; sources?: Sources }) {
  const s = sources?.[id];
  if (!s) return <code className="chip">{id}</code>;
  return (
    <span className="src" tabIndex={0}>
      <code className="chip">{id}</code>
      <span className="pop" role="tooltip"><SourceCard s={s} /></span>
    </span>
  );
}

function Ids({ ids, sources }: { ids: string[]; sources?: Sources }) {
  return <>{ids.length ? ids.map((id) => <Chip key={id} id={id} sources={sources} />) : <span className="muted">none</span>}</>;
}

function PhaseBar({ phase }: { phase: number }) {
  return (
    <div className="phases">
      {PHASES.map((p, i) => (
        <span key={p} className={`phase ${i < phase ? "done" : i === phase ? "active" : ""}`}>
          <i />{p}{i < PHASES.length - 1 && <b />}
        </span>
      ))}
    </div>
  );
}

function Steps({ steps, streaming }: { steps: Step[]; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  const shown = steps.filter((s) => s.status !== "done" || s.label);
  if (!shown.length) return streaming ? <p className="doing">Starting<span className="dots" /></p> : null;
  const expanded = streaming || open;
  // The two agents run in parallel, so their sub-steps arrive interleaved: list each one under its own parent.
  const ordered: Step[] = [];
  for (const s of shown.filter((x) => !x.parent)) ordered.push(s, ...shown.filter((x) => x.parent === s.key && !ordered.includes(x)));
  return (
    <div className="steps">
      {!streaming && <button className="steps-toggle" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} {shown.length} steps</button>}
      {expanded && (
        <ol>
          {ordered.map((s) => (
            <li key={s.id} className={`step ${s.status} ${s.parent ? "sub" : ""}`}>
              <i />
              <span className="step-text">{s.label}{s.status === "running" && <span className="dots" />}{s.status === "stopped" && <em className="muted"> (stopped)</em>}
                {s.detail && <span className="step-detail">{s.detail}</span>}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Recommendation({ req, live, onDecide }: { req: ReviewRequest; live: boolean; onDecide: (d: Decision) => void }) {
  const [overriding, setOverriding] = useState(false);
  const [route, setRoute] = useState<Route>(ROUTES.find((r) => r !== req.route) ?? "manual_escalation");
  const [reason, setReason] = useState("");
  const formRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (overriding) formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [overriding]);
  const d = req.draft;
  return (
    <div className="answer">
      <div className="route-line">
        <span className="eyebrow">Recommended route</span>
        <strong className="route">{ROUTE_LABEL[req.route]}</strong>
        {d && <span className="pill-soft">{d.confidence} confidence</span>}
      </div>
      {!d && <p className="alert">I could not write a draft for this one, so it goes to manual escalation.</p>}
      {req.existing_case_id && <p className="alert">An open case already exists: <code>{req.existing_case_id}</code>. Approving files nothing new.</p>}
      {d && <p className="note">{d.case_note}</p>}
      {req.open_questions.length > 0 && (
        <div className="questions"><span className="eyebrow">What I still need</span>
          <ul>{req.open_questions.map((q) => <li key={q}>{q}</li>)}</ul>
          {live && req.can_answer && <p className="muted small">Reply below and I will look at the account again, once.</p>}
        </div>
      )}
      {d && (
        <div className="sources"><span className="eyebrow">Sources</span>
          <div className="source-row"><span className="muted">Records</span><Ids ids={d.event_ids} sources={req.sources} /></div>
          <div className="source-row"><span className="muted">Policy</span><Ids ids={d.policy_source_ids} sources={req.sources} /></div>
        </div>
      )}
      {live && (
        <div className="actions">
          <button className="primary" onClick={() => onDecide({ action: "approve" })}>Approve · file as {ROUTE_LABEL[req.route]}</button>
          <button className={overriding ? "ghost on" : "ghost"} onClick={() => setOverriding(!overriding)}>Override</button>
        </div>
      )}
      {live && overriding && (
        <div className="subform" ref={formRef}>
          <label>File under
            <select value={route} onChange={(e) => setRoute(e.target.value as Route)}>
              {ROUTES.map((r) => <option key={r} value={r}>{ROUTE_LABEL[r]}</option>)}
            </select>
          </label>
          <label>Reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why the route changes" /></label>
          <button className="primary" disabled={!reason.trim()} onClick={() => onDecide({ action: "override", route, reason: reason.trim() })}>File it</button>
        </div>
      )}
    </div>
  );
}

function Filed({ result, threadId }: { result: ResultEvent; threadId: string | null }) {
  const [trace, setTrace] = useState<string | null>(null);
  const c = result.case, r = result.review;
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
    <div className="answer">
      <div className="route-line"><span className="eyebrow">{c.created ? "Case filed" : "Existing case kept"}</span><code className="case-id">{c.case_id}</code></div>
      <p className="note">Filed under <strong>{ROUTE_LABEL[c.route]}</strong>{r.action === "override" ? <>, on your override: “{r.reason}”</> : ", as recommended and approved by you"}.
        {c.disputed_event_id ? <> The disputed record is <Chip id={c.disputed_event_id} sources={result.sources} />.</> : " No single disputed record was identified."}
        {!c.created && " An open case already covered this, so nothing new was created."}</p>
      <div className="actions">
        <button className="ghost" onClick={viewTrace}>View trace in LangSmith</button>
        {trace && (trace.startsWith("http") ? <a href={trace} target="_blank" rel="noreferrer">open trace ↗</a> : <span className="muted small">trace {trace}</span>)}
      </div>
    </div>
  );
}

function Reply({ turn, index, live, threadId, onDecide }: {
  turn: AssistantTurn; index: number; live: boolean; threadId: string | null; onDecide: (d: Decision) => void;
}) {
  const phase = phaseOf(turn), kind = category(turn.steps);
  return (
    <div className="reply">
      <div className="reply-head"><span className="brand-line">Dispute assistant</span>{kind && <><span className="sep">·</span>{kind}</>}{turn.at && <><span className="sep">·</span>{turn.at}</>}</div>
      {turn.steps.length > 0 && <PhaseBar phase={phase} />}
      <Steps steps={turn.steps} streaming={turn.streaming} />
      {turn.error && (
        <div className="alert error"><p><strong>The run stopped.</strong> {turn.error}</p>
          {live && !turn.pause && !turn.result && <button className="primary" onClick={() => onDecide({ action: "approve" })}>Continue from checkpoint</button>}
        </div>
      )}
      {turn.pause && !turn.result && <Recommendation key={`${index}-${turn.pause.followups}`} req={turn.pause} live={live && !turn.error} onDecide={onDecide} />}
      {turn.result && <Filed result={turn.result} threadId={threadId} />}
    </div>
  );
}

export default function Thread({ thread: t, customer, onSend, onDecide, onDismiss }: {
  thread: ThreadState; customer?: string; onSend: (text: string) => void; onDecide: (d: Decision) => void; onDismiss: () => void;
}) {
  const [text, setText] = useState(t.prefill);
  const endRef = useRef<HTMLDivElement>(null);
  const last = t.turns.at(-1);
  const streaming = last?.role === "assistant" && last.streaming;
  const pause = activePause(t);
  const mode = !t.threadId && !streaming ? "start" : streaming ? "busy" : pause?.can_answer ? "answer" : pause ? "decide" : "done";
  const placeholder = { start: "Describe the complaint as the customer told it to you", busy: "Working on it…",
    answer: "Answer the question above", decide: "Approve or override above to continue", done: "This conversation is complete. Pick another complaint." }[mode];
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [t.turns]);
  function submit() {
    const v = text.trim();
    if (!v || mode === "busy" || mode === "decide" || mode === "done") return;
    onSend(v);
    setText("");
  }
  return (
    <article className="thread">
      <header className="thread-head"><code className="acct big">{t.accountId}</code>{customer && <strong className="customer">{customer}</strong>}<span className="muted">{t.channel}</span></header>
      <div className="turns">
        {t.turns.length === 0 && <p className="hint">Review the complaint in the box below, add anything the customer told you, and send it.</p>}
        {t.turns.map((turn: Turn, i) => turn.role === "rep"
          ? <div key={i} className={`rep ${turn.kind}`}><span className="bubble">{turn.text}</span></div>
          : <Reply key={i} turn={turn} index={i} live={i === t.turns.length - 1} threadId={t.threadId} onDecide={onDecide} />)}
        {t.notice && <p className="notice" onClick={onDismiss}>Not sent: {t.notice}</p>}
        <div ref={endRef} />
      </div>
      <div className="composer">
        <textarea rows={mode === "start" ? 3 : 1} value={text} disabled={mode === "busy" || mode === "decide" || mode === "done"} placeholder={placeholder}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
        <button className="primary" disabled={!text.trim() || mode === "busy" || mode === "decide" || mode === "done"} onClick={submit}>
          {mode === "answer" ? "Reply" : "Send"}
        </button>
      </div>
    </article>
  );
}
