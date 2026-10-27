// The page: the inbox on the left, the conversation on the right.
// One reducer holds it all. A conversation is a list of turns: the rep's messages and the assistant's replies.
import { useCallback, useEffect, useReducer, useState } from "react";
import {
  api, resumeThread, startThread, ROUTE_LABEL,
  type Account, type Decision, type InboxItem, type Owner, type ReviewRequest, type ResultEvent, type StreamEvent, type ThreadSummary,
} from "./api";
import Thread from "./Thread";

export interface Step {
  id: string; key: string; label: string; owner: Owner; parent: string | null; detail: string | null;
  status: "running" | "done" | "stopped";
}
export interface RepTurn { role: "rep"; text: string; kind: "message" | "answer" | "decision" }
export interface AssistantTurn {
  role: "assistant"; steps: Step[]; pause: ReviewRequest | null; result: ResultEvent | null; error: string | null;
  streaming: boolean; at: string;
}
export type Turn = RepTurn | AssistantTurn;
export interface ThreadState {
  threadId: string | null; accountId: string; channel: string; label: string | undefined; prefill: string;
  turns: Turn[]; notice: string | null;
}
interface State { inbox: InboxItem[]; accounts: Account[]; runs: ThreadSummary[]; thread: ThreadState | null }
type Action =
  | { type: "loaded"; inbox?: InboxItem[]; accounts?: Account[]; runs?: ThreadSummary[] }
  | { type: "open"; thread: ThreadState }
  | { type: "threadId"; id: string }
  | { type: "send"; text: string; kind: RepTurn["kind"] }
  | { type: "event"; event: StreamEvent }
  | { type: "failed"; message: string }
  | { type: "dismiss" };

const now = () => new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const stop = (steps: Step[]) => steps.map((s) => (s.status === "running" ? { ...s, status: "stopped" as const } : s));

function onEvent(t: AssistantTurn, e: StreamEvent): AssistantTurn {
  switch (e.type) {
    case "step": {
      if (e.phase === "start") return { ...t, steps: [...t.steps, { ...e, status: "running" }] };
      const i = t.steps.findIndex((s) => s.id === e.id);
      const done: Step = { ...e, status: "done" };
      return { ...t, steps: i < 0 ? [...t.steps, done] : t.steps.map((s, j) => (j === i ? done : s)) };
    }
    case "pause":
      return { ...t, pause: e.request, streaming: false, steps: t.steps.filter((s) => s.key !== "review" || s.status === "done") };
    case "result":
      return { ...t, result: e, streaming: false };
    case "error":
      return { ...t, error: e.message, streaming: false, steps: stop(t.steps) };
  }
}

function withLast(t: ThreadState, f: (a: AssistantTurn) => AssistantTurn): ThreadState {
  const i = t.turns.length - 1, last = t.turns[i];
  return last?.role === "assistant" ? { ...t, turns: [...t.turns.slice(0, i), f(last)] } : t;
}

function reducer(state: State, a: Action): State {
  const t = state.thread;
  switch (a.type) {
    case "loaded":
      return { ...state, ...(a.inbox && { inbox: a.inbox }), ...(a.accounts && { accounts: a.accounts }), ...(a.runs && { runs: a.runs }) };
    case "open":
      return { ...state, thread: a.thread };
    case "threadId":
      return t ? { ...state, thread: { ...t, threadId: a.id } } : state;
    case "send": {
      if (!t) return state;
      const reply: AssistantTurn = { role: "assistant", steps: [], pause: null, result: null, error: null, streaming: true, at: now() };
      const turns = [...t.turns, { role: "rep", text: a.text, kind: a.kind } as RepTurn, reply];
      return { ...state, thread: { ...t, turns, notice: null, prefill: "" } };  // prefill is spent once sent
    }
    case "event":
      return t ? { ...state, thread: withLast(t, (last) => onEvent(last, a.event)) } : state;
    case "failed": { // an HTTP refusal: nothing ran, so take the turn back and say why
      if (!t) return state;
      const last = t.turns[t.turns.length - 1];
      if (last?.role === "assistant" && last.steps.length === 0)
        return { ...state, thread: { ...t, turns: t.turns.slice(0, -2), notice: a.message } };
      return { ...state, thread: withLast(t, (l) => ({ ...l, error: a.message, streaming: false, steps: stop(l.steps) })) };
    }
    case "dismiss":
      return t ? { ...state, thread: { ...t, notice: null } } : state;
  }
}

/** The pause the rep can still act on: the latest assistant turn, when it is waiting. */
export function activePause(t: ThreadState): ReviewRequest | null {
  const last = t.turns[t.turns.length - 1];
  return last?.role === "assistant" && !last.streaming && !last.error ? last.pause : null;
}

function age(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return mins < 60 ? `${mins}m ago` : mins < 1440 ? `${Math.round(mins / 60)}h ago` : `${Math.round(mins / 1440)}d ago`;
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, { inbox: [], accounts: [], runs: [], thread: null });
  const [picking, setPicking] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const t = state.thread;
  const busy = t?.turns[t.turns.length - 1]?.role === "assistant" && (t.turns[t.turns.length - 1] as AssistantTurn).streaming;

  const refreshRuns = useCallback(() => {
    api.threads().then((runs) => dispatch({ type: "loaded", runs })).catch(() => undefined);
  }, []);
  useEffect(() => {
    Promise.all([api.inbox(), api.accounts()])
      .then(([inbox, accounts]) => dispatch({ type: "loaded", inbox, accounts }))
      .catch((e: Error) => setLoadError(`Cannot reach the server: ${e.message}. Is \`python -m app.server\` running?`));
    refreshRuns();
  }, [refreshRuns]);

  const run = (p: Promise<void>) => p.catch((e: Error) => dispatch({ type: "failed", message: e.message })).finally(refreshRuns);
  const ev = (event: StreamEvent) => dispatch({ type: "event", event });
  const openWith = (accountId: string, channel: string, prefill: string, label?: string) =>
    dispatch({ type: "open", thread: { threadId: null, accountId, channel, label, prefill, turns: [], notice: null } });

  function send(text: string) {
    if (!t) return;
    const pause = activePause(t);
    if (pause?.can_answer && t.threadId) {
      dispatch({ type: "send", text, kind: "answer" });
      run(resumeThread(t.threadId, { action: "answer", answer: text }, ev));
    } else if (!t.threadId) {
      dispatch({ type: "send", text, kind: "message" });
      run(startThread(t.accountId, text, t.label, (id) => dispatch({ type: "threadId", id }), ev));
    }
  }
  function decide(d: Decision) {
    if (!t?.threadId) return;
    const text = d.action === "approve" ? "Approved. File it under the recommended route." : d.action === "override"
      ? `Override: file it under ${ROUTE_LABEL[d.route]}. ${d.reason}` : "Continue from the last checkpoint.";
    dispatch({ type: "send", text, kind: "decision" });
    run(resumeThread(t.threadId, d, ev));
  }
  async function reopen(s: ThreadSummary) {
    try {
      const d = await api.thread(s.thread_id);
      const reply: AssistantTurn = { role: "assistant", steps: [], pause: d.request, result: d.result, streaming: false, at: "",
        error: d.status === "stopped" ? `This run stopped at ${d.stopped_at.join(", ")} before reaching a decision.` : null };
      dispatch({ type: "open", thread: { threadId: d.thread_id, accountId: d.account_id ?? "", channel: "Reopened", label: d.label,
        prefill: "", notice: null, turns: [{ role: "rep", text: d.message ?? "", kind: "message" }, reply] } });
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }

  return (
    <div className="desk">
      <aside className="inbox">
        <header className="brand">
          <span className="brand-mark">AMF</span>
          <div><h1>Dispute desk</h1><p>Agarwal Motor Finance · servicing</p></div>
        </header>
        {loadError && <p className="notice">{loadError}</p>}
        <section>
          <h2>Inbox <span className="count">{state.inbox.length}</span></h2>
          <ul className="queue">
            {state.inbox.map((item, i) => (
              <li key={item.scenario_id} style={{ animationDelay: `${i * 30}ms` }}>
                <button disabled={busy} onClick={() => openWith(item.account_id, item.channel, item.message, item.scenario_id)}
                  className={t?.label === item.scenario_id ? "active" : ""}>
                  <span className="row-top"><code className="acct">{item.account_id}</code><span className="channel">{item.channel}</span></span>
                  <span className="snippet">{item.message}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section className="call">
          {!picking ? (
            <button className="ghost" disabled={busy} onClick={() => setPicking(true)}>+ New conversation</button>
          ) : (
            <label>Which account is calling?
              <select autoFocus defaultValue="" onChange={(e) => { if (e.target.value) { openWith(e.target.value, "Phone call", ""); setPicking(false); } }}>
                <option value="">Choose an account</option>
                {state.accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.account_id}</option>)}
              </select>
            </label>
          )}
        </section>
        <section>
          <h2>Recent runs <span className="count">{state.runs.filter((r) => r.status !== "finished").length} open</span></h2>
          <ul className="runs">
            {state.runs.map((r) => (
              <li key={r.thread_id}>
                <button disabled={busy} onClick={() => reopen(r)} className={t?.threadId === r.thread_id ? "active" : ""}>
                  <span className="row-top"><code className="acct">{r.account_id}</code><span className="muted">{age(r.updated_at)}</span></span>
                  <span className="run-meta">
                    <span className={`status ${r.status}`}>{r.status === "stopped" ? `stopped at ${r.stopped_at[0]}` : r.status}</span>
                    {r.route && <span>{ROUTE_LABEL[r.route]}</span>}
                  </span>
                </button>
              </li>
            ))}
            {state.runs.length === 0 && <li className="muted empty">No runs yet.</li>}
          </ul>
        </section>
      </aside>

      <main className="pane">
        {t ? (
          <Thread key={`${t.accountId}-${t.label ?? ""}-${t.threadId ?? "new"}`} thread={t} onSend={send} onDecide={decide}
            onDismiss={() => dispatch({ type: "dismiss" })} />
        ) : (
          <div className="blank">
            <h2>Pick a complaint from the inbox</h2>
            <p>Send it to the assistant as you would describe it to a colleague. It reads the account and the policy,
              cross-checks them, and recommends a route. Nothing is filed until you decide.</p>
          </div>
        )}
      </main>

    </div>
  );
}
