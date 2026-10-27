// The page: the inbox column on the left, the thread on the right, and the one reducer that holds both.
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
export interface ThreadState {
  threadId: string | null; customer: string; accountId: string; channel: string; customerMessage: string;
  steps: Step[]; pause: ReviewRequest | null; result: ResultEvent | null; error: string | null; streaming: boolean;
}
interface State { inbox: InboxItem[]; accounts: Account[]; paused: ThreadSummary[]; thread: ThreadState | null }
type Action =
  | { type: "loaded"; inbox?: InboxItem[]; accounts?: Account[]; paused?: ThreadSummary[] }
  | { type: "open"; thread: ThreadState }
  | { type: "threadId"; id: string }
  | { type: "event"; event: StreamEvent }
  | { type: "resuming" }
  | { type: "failed"; message: string };

function stopRunning(steps: Step[], keep?: (s: Step) => boolean): Step[] {
  return steps.filter((s) => s.status !== "running" || !keep || keep(s)).map((s) => (s.status === "running" ? { ...s, status: "stopped" } : s));
}

function onEvent(t: ThreadState, e: StreamEvent): ThreadState {
  switch (e.type) {
    case "step": {
      if (e.phase === "start") return { ...t, steps: [...t.steps, { ...e, status: "running" }] };
      const i = t.steps.findIndex((s) => s.id === e.id);
      const done: Step = { ...e, status: "done", label: e.label };
      return { ...t, steps: i < 0 ? [...t.steps, done] : t.steps.map((s, j) => (j === i ? done : s)) };
    }
    case "pause": // the pause card stands in for the "waiting for your decision" line
      return { ...t, threadId: e.thread_id, pause: e.request, streaming: false, steps: stopRunning(t.steps, (s) => s.key !== "review") };
    case "result":
      return { ...t, result: e, pause: null, streaming: false };
    case "error":
      return { ...t, error: e.message, pause: null, streaming: false, steps: stopRunning(t.steps) };
  }
}

function reducer(state: State, a: Action): State {
  const t = state.thread;
  switch (a.type) {
    case "loaded":
      return { ...state, ...(a.inbox && { inbox: a.inbox }), ...(a.accounts && { accounts: a.accounts }), ...(a.paused && { paused: a.paused }) };
    case "open":
      return { ...state, thread: a.thread };
    case "threadId":
      return t ? { ...state, thread: { ...t, threadId: a.id } } : state;
    case "event":
      return t ? { ...state, thread: onEvent(t, a.event) } : state;
    case "resuming":
      return t ? { ...state, thread: { ...t, error: null, streaming: true } } : state;  // the pause stays until the server answers
    case "failed": // an HTTP refusal (404/409/422): nothing ran, so the pause stays as it was
      return t ? { ...state, thread: { ...t, error: a.message, streaming: false } } : state;
  }
}

const fresh = (customer: string, accountId: string, channel: string, message: string): ThreadState => ({
  threadId: null, customer, accountId, channel, customerMessage: message, steps: [], pause: null, result: null, error: null, streaming: true,
});

function age(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return mins < 60 ? `${mins}m ago` : mins < 1440 ? `${Math.round(mins / 60)}h ago` : `${Math.round(mins / 1440)}d ago`;
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, { inbox: [], accounts: [], paused: [], thread: null });
  const [calling, setCalling] = useState(false);
  const [callAccount, setCallAccount] = useState("");
  const [callNotes, setCallNotes] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const busy = state.thread?.streaming ?? false;
  const customerOf = (id: string | null) => state.accounts.find((a) => a.account_id === id)?.customer ?? id ?? "";

  const refreshThreads = useCallback(() => {
    api.threads().then((paused) => dispatch({ type: "loaded", paused })).catch(() => undefined);
  }, []);
  useEffect(() => {
    Promise.all([api.inbox(), api.accounts()])
      .then(([inbox, accounts]) => dispatch({ type: "loaded", inbox, accounts }))
      .catch((e: Error) => setLoadError(`Cannot reach the server: ${e.message}. Is \`python -m app.server\` running?`));
    refreshThreads();
  }, [refreshThreads]);

  const run = (promise: Promise<void>) =>
    promise.catch((e: Error) => dispatch({ type: "failed", message: e.message })).finally(refreshThreads);
  const ev = (event: StreamEvent) => dispatch({ type: "event", event });

  function open(item: InboxItem) {
    dispatch({ type: "open", thread: fresh(item.customer, item.account_id, item.channel, item.message) });
    run(startThread(item.account_id, item.message, item.scenario_id, (id) => dispatch({ type: "threadId", id }), ev));
  }
  function logCall() {
    const account = state.accounts.find((a) => a.account_id === callAccount);
    if (!account || !callNotes.trim()) return;
    dispatch({ type: "open", thread: fresh(account.customer, account.account_id, "Phone call (logged by you)", callNotes.trim()) });
    run(startThread(account.account_id, callNotes.trim(), undefined, (id) => dispatch({ type: "threadId", id }), ev));
    setCalling(false);
    setCallNotes("");
  }
  function decide(decision: Decision) {
    const id = state.thread?.threadId;
    if (!id) return;
    dispatch({ type: "resuming" });
    run(resumeThread(id, decision, ev));
  }
  async function reopen(s: ThreadSummary) {
    try {
      const d = await api.thread(s.thread_id);
      dispatch({ type: "open", thread: {
        ...fresh(customerOf(d.account_id), d.account_id ?? "", "Reopened from paused runs", d.message ?? ""),
        threadId: d.thread_id, streaming: false, pause: d.request, result: d.result,
        error: d.status === "stopped" ? `This run stopped at ${d.stopped_at.join(", ")} before reaching review.` : null,
      } });
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
                <button disabled={busy} onClick={() => open(item)}
                  className={state.thread?.customerMessage === item.message ? "active" : ""}>
                  <span className="row-top"><strong>{item.customer}</strong><code>{item.account_id}</code></span>
                  <span className="channel">{item.channel}</span>
                  <span className="snippet">{item.message}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="call">
          {!calling ? (
            <button className="ghost" disabled={busy} onClick={() => setCalling(true)}>+ Log a phone call</button>
          ) : (
            <div className="call-form">
              <label>Account
                <select value={callAccount} onChange={(e) => setCallAccount(e.target.value)}>
                  <option value="">Choose the caller's account</option>
                  {state.accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.account_id} · {a.customer}</option>)}
                </select>
              </label>
              <label>Call notes
                <textarea rows={4} value={callNotes} onChange={(e) => setCallNotes(e.target.value)}
                  placeholder="What the customer said, in your words" />
              </label>
              <div className="row">
                <button className="primary" disabled={busy || !callAccount || !callNotes.trim()} onClick={logCall}>Start investigation</button>
                <button className="ghost" onClick={() => setCalling(false)}>Cancel</button>
              </div>
            </div>
          )}
        </section>

        <section>
          <h2>Paused runs <span className="count">{state.paused.filter((p) => p.status !== "finished").length}</span></h2>
          <ul className="runs">
            {state.paused.map((p) => (
              <li key={p.thread_id}>
                <button disabled={busy} onClick={() => reopen(p)} className={state.thread?.threadId === p.thread_id ? "active" : ""}>
                  <span className="row-top"><code>{p.thread_id}</code></span>
                  <span className="run-meta">
                    <span className={`status ${p.status}`}>{p.status === "stopped" ? `stopped at ${p.stopped_at[0]}` : p.status}</span>
                    {p.route && <span>{ROUTE_LABEL[p.route]}</span>}
                    <span className="muted">{age(p.updated_at)}</span>
                  </span>
                </button>
              </li>
            ))}
            {state.paused.length === 0 && <li className="muted empty">No runs yet.</li>}
          </ul>
        </section>
      </aside>

      <main className="pane">
        {state.thread ? <Thread thread={state.thread} onDecide={decide} /> : (
          <div className="blank">
            <h2>Pick a complaint from the inbox</h2>
            <p>The assistant reads the account and the policy, cross-checks them, and drafts a review case.
              Every step is marked <b className="badge ai">AI</b> or <b className="badge code">code</b>, and nothing is filed until <b className="badge you">you</b> decide.</p>
          </div>
        )}
      </main>
    </div>
  );
}
