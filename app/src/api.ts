// The server's payloads and events (docs/history/web-ui.md §7), and the Server-Sent Events reader.
// EventSource is GET-only and a run needs a POST body, so the stream is read with fetch + ReadableStream.

export type Owner = "ai" | "code" | "you";
export type Route = "adjustment_review" | "explain_events" | "need_more_evidence" | "manual_escalation";
export const ROUTES: Route[] = ["adjustment_review", "explain_events", "need_more_evidence", "manual_escalation"];
export const ROUTE_LABEL: Record<Route, string> = {
  adjustment_review: "Adjustment review",
  explain_events: "Explain the events",
  need_more_evidence: "Need more evidence",
  manual_escalation: "Manual escalation",
};

export interface InboxItem { scenario_id: string; account_id: string; customer: string; channel: string; message: string }
export interface Account { account_id: string; customer: string }
export interface ThreadSummary {
  thread_id: string; label: string; account_id: string | null; status: "paused" | "finished" | "stopped";
  route: Route | null; updated_at: string | null; stopped_at: string[];
}
export interface Draft {
  review_route: Route; event_ids: string[]; policy_source_ids: string[];
  confidence: "high" | "medium" | "low"; open_questions: string[]; case_note: string;
}
export interface ReviewRequest {
  thread_label: string; route: Route; existing_case_id: string | null;
  draft: Draft | null; open_questions: string[]; can_answer: boolean;
  followups: number;
}
export interface CaseResult {
  case_id: string; created: boolean; account_id: string; category: string; disputed_event_id: string | null; route: Route;
}
export type Decision =
  | { action: "approve" }
  | { action: "override"; route: Route; reason: string }
  | { action: "answer"; answer: string };

export interface StepEvent {
  type: "step"; phase: "start" | "end"; id: string; key: string; label: string; owner: Owner;
  parent: string | null; detail: string | null;
}
export interface PauseEvent { type: "pause"; thread_id: string; request: ReviewRequest }
export interface ResultEvent {
  type: "result"; thread_id: string; case: CaseResult; review: Decision & { route?: Route | null; reason?: string | null };
  draft: Draft | null; route: Route;
}
export interface ErrorEvent { type: "error"; thread_id: string; message: string }
export type StreamEvent = StepEvent | PauseEvent | ResultEvent | ErrorEvent;

export interface ThreadDetail extends ThreadSummary {
  message: string | null; request: ReviewRequest | null; result: ResultEvent | null;
}

async function failure(r: Response): Promise<Error> {
  try {
    const body = await r.json();
    const detail = Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : body.detail;
    return new Error(String(detail ?? r.statusText));
  } catch {
    return new Error(`${r.status} ${r.statusText}`);
  }
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw await failure(r);
  return r.json() as Promise<T>;
}

export const api = {
  inbox: () => get<{ items: InboxItem[] }>("/api/inbox").then((b) => b.items),
  accounts: () => get<{ items: Account[] }>("/api/accounts").then((b) => b.items),
  threads: () => get<{ items: ThreadSummary[] }>("/api/threads").then((b) => b.items),
  thread: (id: string) => get<ThreadDetail>(`/api/threads/${encodeURIComponent(id)}`),
  trace: (id: string) => get<{ url: string }>(`/api/threads/${encodeURIComponent(id)}/trace`).then((b) => b.url),
};

/** POST, then call onEvent for every `data:` line until the server closes the stream. Rejects on an HTTP error. */
export async function stream(
  url: string, body: unknown, onThreadId: (id: string) => void, onEvent: (e: StreamEvent) => void,
): Promise<void> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok || !r.body) throw await failure(r);
  const id = r.headers.get("X-Thread-Id");
  if (id) onThreadId(id);
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of chunk.split("\n")) if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
    }
  }
}

export const startThread = (account_id: string, message: string, label: string | undefined,
  onThreadId: (id: string) => void, onEvent: (e: StreamEvent) => void) =>
  stream("/api/threads", { account_id, message, label }, onThreadId, onEvent);

export const resumeThread = (id: string, decision: Decision, onEvent: (e: StreamEvent) => void) =>
  stream(`/api/threads/${encodeURIComponent(id)}/resume`, decision, () => undefined, onEvent);
