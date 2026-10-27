# Web UI: the rep's inbox and assistant

## 1. Status

**v1.1 | October 26, 2026.** New slice; supersedes nothing. v1.1 same day: everything under one `app/` folder, 14 files (W13). Builds on architecture v0.7.1 (commit 2df4722, one folder per diagram box) and the human-review slice (ae147f1). Nothing in the graph, the agents, the gate or the eval changes behaviour.

Two phases, each a vertical slice one agent can finish in one loop. **Phase A** (backend: the event stream and its tests) must be green before **Phase B** (the React page) starts.

## 2. Why & goal

**The bet:** a reviewer who watches the agent work step by step, in plain words, with "AI" and "code" badged on each step and a pause where a person decides, understands the trust story in 60 seconds, faster than reading `ask`'s 80-line dump.

**The one use case:** a servicing rep opens the inbox, clicks the complaint from Priya Natarajan on account A-1002 ("cust got hit w a late fee on oct but says he paid over the phone last tues"), watches the assistant read the account, read the policy, cross-check and draft, sees the gate's verdict and the recommended route, and clicks **Approve**. The case is filed. On A-1016 ("where is my refund?? been weeks") the route is `need_more_evidence`; the rep types the answer in the thread and watches the agent look again.

**This plan covers:** a localhost page (Vite + React) and a small FastAPI server that streams the graph's steps. Nothing else.

**Realism rule (decided with Rachit, Oct 26):** the rep never retypes a complaint. Complaints already sit in an inbox tied to an account, as they would after arriving through the customer portal or a call screen. The seeded inbox is the 12 dev scenarios' complaint texts; "Log a phone call" covers new complaints, where the rep types call notes. The 4 held-out scenarios never appear in the UI.

## 3. Definition of Done

### Acceptance criteria (each must exit 0)

| # | Command | Proves |
|---|---|---|
| A1 | `.venv/bin/pytest tests/test_narrate.py -q` | Debug-stream chunks from the fake graph become the exact step sequence the UI shows, for the clean path, the clarify retry, and the answer follow-up |
| A2 | `.venv/bin/pytest tests/test_server.py -q` | The HTTP contract: inbox excludes held-out and answer-key fields; a POST streams `step`, `pause`, `result` events in order; resume with a refused answer is a 422; thread list reports paused/finished |
| A3 | `.venv/bin/pytest -q` | No regression: the existing 190 tests still pass, no LLM calls |
| A4 | `cd app && npm ci && npm run build` | TypeScript compiles (`tsc -b`) and Vite builds the page |
| A5 | `cd app && npm run lint` | `tsc --noEmit` only (no ESLint added): the type check runs without a build |
| A6 | `.venv/bin/python -c "import app.server"` | The server module imports with no keys set (keys are needed only when a run starts) |

**Manual smoke (not gating, needs API keys, max 6 live runs ≈ $1.50):** `python -m app.server` + `cd app && npm run dev`; run A-1002 (approve path), A-1016 (answer path), A-1008 (S-07, the wrong-account trap, expect `manual_escalation` with a gate note), one "Log a phone call" complaint, one override, and reopen one paused thread from the list. Record the outcome in the final report; do not fix agent behaviour found here (that is a separate slice).

### Budgets

| Budget | Max |
|---|---|
| New files | 14, all listed in §4: 2 Python, 2 tests, 10 in `app/` for the page (5 of those are Vite boilerplate: `package.json`, the lockfile, `tsconfig.json`, `vite.config.ts`, `index.html`) |
| LOC per new file | 250 (`server.py`, `App.tsx` and `Thread.tsx` are the ones at risk; the lockfile is exempt, it is generated) |
| New DB tables | 0 (the SQLite checkpoint file is LangGraph's; it is read, never altered) |
| New DB columns | 0 |
| New metric / event names | 4 SSE event types: `step`, `pause`, `result`, `error`. No more |
| New Python dependencies | 2: `fastapi`, `uvicorn` |
| New npm dependencies | 2 runtime (`react`, `react-dom`) + 5 dev (`vite`, `@vitejs/plugin-react`, `typescript`, `@types/react`, `@types/react-dom`) |
| Changes to graph nodes, prompts, Pydantic output schemas | 0 |
| Live LLM runs during the build | 6 |

Exceeding any of these is a stop-and-ask.

### Must-handle edge cases

| Case | Required behaviour |
|---|---|
| Synthesize returned no draft (`synthesis.draft is None`) | The pause card says "The assistant could not write a draft" with the gate's reasons; route shows `manual_escalation`; Approve and Override still work; no Answer box |
| Gate rejected the draft | Card shows the gate's route, a "Gate overrode the draft" badge and every reason; the LLM's original route is shown struck through |
| `existing_case_id` set (duplicate) | Card says "An open case already exists: C-002. Approving files nothing new"; the result card shows `created: false` |
| `can_answer` false (route is not `need_more_evidence`, or the one answer is used) | The Answer box is not rendered; a server-side `answer` on such a pause returns 422 and the UI shows the message |
| Account Agent retry / Policy Agent re-retrieve / rep follow-up | Each shows its own plain-language line ("looking again, once"), never the first-run label |
| Run crashes mid-way (API error, network) | The thread shows an `error` event with the message and a "Continue from checkpoint" button that resumes with approve, which (per `run.py`) re-runs the failed node and pauses at review again |
| Browser refresh during a run | The run keeps going on the server; the thread reappears in the "Paused runs" list once it pauses; its step trail is gone (that is accepted, the trace link remains) |
| Unknown node name in the stream | `narrate` uses the raw name as the label with owner `code`; nothing throws |
| Unknown `account_id` on "Log a phone call" | 404 with the known IDs; the UI only offers the dropdown so this is a server test, not a UI path |
| Second send while a run streams | The composer and inbox clicks are disabled until `pause`, `result` or `error` arrives |

### Out of scope

**Not building:** authentication, a customer-facing persona, editing the case note or citations in the UI, a second simultaneous conversation, mobile layout, dark mode, persistence of the step trail, notifications, deploying anywhere but localhost, any change to how the agent decides anything, any eval or scenario change, running the held-out split.

**Tempting additions forbidden by name:**
- No state library (`redux`, `zustand`, `@tanstack/react-query`, `swr`): React `useState`/`useReducer` only.
- No CSS framework or component library (`tailwind`, `mui`, `chakra`, `shadcn`, `styled-components`): one `styles.css` with CSS variables.
- No `sse-starlette`, `websockets`, `socket.io`: plain `StreamingResponse` with `text/event-stream`, read by `fetch` + `ReadableStream`.
- No `AsyncSqliteSaver`, no `astream_events`: the sync `graph.stream(...)` in a worker thread, exactly what `tests/test_graph.py` already uses.
- No new graph node, no `get_stream_writer()` call inside the agents unless the Phase A spike fails (then stop and ask; see §11).
- No LangSmith API call in the run path: the trace URL is fetched lazily when the link is clicked.
- No server flags beyond `--data`, `--port`: no config surface.
- No changes to `Draft`, `GateResult`, `ReviewRequest`, `ReviewDecision`: the UI renders them as they are.
- No React tests, no Playwright: the contract is tested in Python; the page is checked by `tsc` and the manual smoke.

## 4. File inventory (closed)

Everything new lives under one folder, `app/`: the Python server next to the page it serves. The agent package `dispute_agent/` gains nothing.

```text
app/
  server.py          FastAPI app + the stream bridge          (Python)
  narrate.py         the status key: stream chunks -> UI events (Python, pure)
  package.json, package-lock.json, tsconfig.json, vite.config.ts, index.html   (Vite boilerplate)
  src/
    main.tsx         mounts App
    App.tsx          layout, the one reducer, the inbox column
    Thread.tsx       the chat column: step trail, pause actions, case card
    api.ts           the event/payload types and the SSE reader
    styles.css
tests/
  test_narrate.py, test_server.py
```

### Phase A: backend

| Path | Action | Why this file |
|---|---|---|
| `app/narrate.py` | CREATE | The status key: turns LangGraph debug/updates stream chunks into UI events. Pure functions, no I/O |
| `app/server.py` | CREATE | FastAPI app factory `make_app(ds, sections, graph)`; the six routes; the worker-thread-to-SSE bridge; `python -m app.server` entry |
| `requirements.txt` | UPDATE | `fastapi>=0.115,<1`, `uvicorn>=0.30,<1` |
| `tests/test_narrate.py` | CREATE | A1 |
| `tests/test_server.py` | CREATE | A2, via `fastapi.testclient.TestClient` with the fakes from `test_graph.py` |

### Phase B: page

| Path | Action | Why this file |
|---|---|---|
| `app/package.json`, `app/package-lock.json` | CREATE | Vite scaffold; scripts `dev`, `build`, `lint` (`tsc --noEmit`) |
| `app/tsconfig.json` | CREATE | strict TS |
| `app/vite.config.ts` | CREATE | React plugin; dev proxy `/api` → `http://127.0.0.1:8000` |
| `app/index.html` | CREATE | Entry |
| `app/src/main.tsx` | CREATE | Mounts `App` (6 lines; Vite needs a script entry separate from `index.html`) |
| `app/src/App.tsx` | CREATE | Layout, the one `useReducer`, the inbox column (queue, "Log a phone call", paused runs) |
| `app/src/Thread.tsx` | CREATE | The chat column: customer bubble, step trail with the "…" line and AI/code/you badges, the pause card with Approve · Override · Answer, the result card with the trace link |
| `app/src/api.ts` | CREATE | The event and payload types (mirroring §7 one-to-one) and the SSE reader (`fetch` + `ReadableStream`) |
| `app/src/styles.css` | CREATE | Variables, layout, the dots animation |

No `app/.gitignore`: the root `.gitignore` gains `app/node_modules/` and `app/dist/`. No `__init__.py`: `app` is an implicit namespace package, which `python -m app.server` and `import app.narrate` both accept on Python 3.12.

### Docs

| Path | Action | Why |
|---|---|---|
| `README.md` | UPDATE | "Web app" section under *Run it* (`python -m app.server`, `cd app && npm run dev`); `app/` in the repo layout block; an `app/` row in the box table ("outside the run", like `evaluation/`) |
| `docs/architecture.md` | UPDATE | "Deliberately not in v1": replace "No UI, streaming" with one line: a localhost page streams the graph's steps and hosts the review pause; nothing in the graph changed. Version history line v0.7.2 |
| `.gitignore` | UPDATE | `app/node_modules/`, `app/dist/` |

Touching any other file is a scope breach: stop and ask. (`dispute_agent/orchestrator/graph.py` is the one conditional exception, §11 F1.)

### Read first

- [`dispute_agent/orchestrator/graph.py:60-222`](../../dispute_agent/orchestrator/graph.py): node names, `after_sync`, `after_review`, `build_graph` signature, `sqlite_checkpointer`, `initial_graph_state`
- [`dispute_agent/orchestrator/run.py`](../../dispute_agent/orchestrator/run.py): `thread_config`, `_outcome`, `thread_status`; the server mirrors `start`/`resume` with `stream`
- [`dispute_agent/review/review.py`](../../dispute_agent/review/review.py): `ReviewRequest`, `ReviewDecision` (the validator is the server's 422)
- [`dispute_agent/__main__.py:88-200`](../../dispute_agent/__main__.py): `_print_sections`, `cmd_ask`, `cmd_resume` (the thread checks the server copies)
- [`tests/test_graph.py:1-60`](../../tests/test_graph.py): `FakeAccountAgent`, `run()`, `NORMAL`; the test fakes the server tests reuse
- [`dispute_agent/account_agent/agent.py:136-196`](../../dispute_agent/account_agent/agent.py): the inner nodes `llm_call`, `tool_node`, `finalize` whose names the narrator keys on
- [`dispute_agent/data.py:90-115`](../../dispute_agent/data.py): `Scenario` (the inbox must never serialise `expected_*` or `rationale`)

## 5. Closed decisions (do not re-litigate)

| # | Decision | Why this, not X |
|---|---|---|
| **W1** | **Inbox + assistant.** The complaint is already in the system, tied to an account; the rep clicks it | Not a paste box: a rep never retypes a complaint, and the account ID "comes from the rep's screen, never guessed" (architecture §1). Not customer self-service: the route, gate verdict and case note are written for a rep, and the human decision is the trust story |
| **W2** | **Status key is a static table in `app/narrate.py` keyed on the graph's node names and the Account Agent's tool names**, the same names LangSmith shows | Not the LangSmith API at runtime: adds seconds of ingestion lag and a network call per step for information the local stream already has. The trace link (W8) gives the reviewer the LangSmith view on demand |
| **W3** | **Sync `graph.stream(..., stream_mode=["debug", "updates"], subgraphs=True)` in a worker thread, bridged to SSE through a queue** | `debug` emits `task` (node started) and `task_result` (node finished); `updates` carries `__interrupt__`; `subgraphs=True` surfaces the Account Agent's inner `tool_node` runs. Not `astream_events`: needs the async checkpointer and a second graph shape. Not `get_stream_writer()` in the agents: touches agent code for something the stream already provides (fallback only, §11 F1) |
| **W4** | **FastAPI + uvicorn; SSE over `StreamingResponse`; the browser reads it with `fetch` + `ReadableStream`** | `EventSource` is GET-only and the run needs a POST body. `fetch` streaming is ~30 lines and needs no library |
| **W5** | **One `make_app(ds, sections, graph)` factory; `python -m app.server` builds the real graph with the SQLite checkpointer at `runs/checkpoints.sqlite`, the same file as the CLI.** The CLI's `__main__.py` is not touched | Tests inject the fake graph; the CLI and the page share paused threads, so `resume` from either works |
| **W6** | **Every step carries an `owner`: `ai`, `code` or `you`**, rendered as a badge | This is the brief's "language goes to the LLM, rules go to code" made visible per step; it costs one field |
| **W7** | **The pause offers all three rep actions in the thread** (approve, override with route + reason, answer when `can_answer`) | Rachit chose all three; each is a small form, and the answer path is the best live demo |
| **W8** | **Trace link fetched lazily:** the server records the `run_id` it passed in the config; `GET /api/threads/{id}/trace` calls `langsmith.Client().read_run(run_id).url` when clicked | LangSmith ingests asynchronously; reading at run end can 404. Lazy fetch also keeps LangSmith out of the run path (banned, §3) |
| **W9** | **Inbox = dev scenarios only; it serialises `scenario_id`, `account_id`, customer name, `rep_message`, and a channel label derived from `wording`** (`clean` → "Customer message", `messy` → "Call notes") | Held-out scenarios must stay unrun until the last day. `expected_*` fields and `rationale` are the answer key and never leave the server |
| **W10** | **Plain CSS, no component library, no state library** | The page is 6 components; a library would be the largest file in the repo for no behaviour |
| **W11** | **The step trail is not persisted.** A paused thread reopened from the list shows the pause card only | Persisting events means a store (banned). The LangSmith trace is the durable record |
| **W12** | **Plan lives in `docs/plans/`** like every other slice | The repo moved plans out of `tasks/` on Oct 26 |
| **W13** | **Everything new lives in `app/`, server included; 14 files, three React source files** (Rachit, Oct 26) | Not `dispute_agent/web/` + `web/`: the server exists only for the page, so they ship as one folder. Not one component per widget: six components of 40 lines each is more files than ideas; `App.tsx` (inbox side) and `Thread.tsx` (chat side) split on the two columns the page actually has |

## 6. Architecture

### No new model step

This plan adds no LLM call. The code-vs-model table is therefore about UI fields only, and every owner is code:

| UI field | Where it comes from | Owner |
|---|---|---|
| Step label, owner badge, detail line | `narrate.py` table keyed on node/tool name; detail read from the `task_result` payload (category, tool call count, gate route) | code |
| Pause card (route, gate verdict, reasons, draft, open questions, `can_answer`) | `ReviewRequest`, the interrupt payload, unchanged | code (rendering); the draft's text is the LLM's, shown verbatim |
| Result card (`case_id`, `created`, final route, who decided) | `CaseResult` + `ReviewDecision` from final state | code |
| Inbox rows | `Dataset.scenarios` (dev split) + `Dataset.accounts` | code |
| Paused-runs list | `checkpointer.list(None)` grouped by `thread_id`, status via `graph.get_state` | code |
| Trace URL | `langsmith.Client().read_run(run_id).url` | code |

### The status key (W2, W6)

`narrate.py` holds one table. `key` is the node name as LangSmith shows it; tool rows are keyed `tool:<name>`. `start` is shown with the "…" while the step runs; `done` is the collapsed line afterwards, with `{placeholders}` filled from the `task_result` payload where cheap.

| key | owner | start | done |
|---|---|---|---|
| `classify` | ai | Working out what kind of dispute this is | Dispute type: {category, humanised} |
| `account_agent` | ai | Reading the account's records | Account records read ({n} lookups) |
| `account_agent/llm_call` | ai | Deciding what to look up next | (hidden when done) |
| `tool:get_account` | code | Pulling the account details | Account details pulled |
| `tool:list_events` | code | Pulling the payment history | Payment history pulled |
| `tool:get_event` | code | Looking at record {event_id} | Record {event_id} read |
| `tool:find_events_by_reference` | code | Searching for reference "{reference}" across accounts | Reference search done |
| `tool:list_review_cases` | code | Checking for an existing review case | Existing cases checked |
| `account_agent/finalize` | ai | Summarising what the records show | Records summarised |
| `derive_states` | code | Working out the payment and refund status | Payment state: {payment_state}; refund state: {refund_state} |
| `policy_agent` | ai | Finding the policy in force on Oct 20, 2026 and reading it | Policy read: {n} rules from {m} sections |
| `sync` | code | Cross-checking the account findings against the policy | Cross-check: {"no issues" or the issues joined} |
| `account_agent_retry` | ai | The account search came back empty; looking again (once) | Account records read again |
| `derive_states_retry` | code | Working out the payment and refund status again | (as derive_states) |
| `policy_agent_retry` | ai | A different policy was in force on {policy_date}; re-reading it (once) | Policy re-read on {policy_date} |
| `synthesize` | ai | Drafting the review case | Draft written: route {route} |
| `gate` | code | Checking the draft against our policy rules | Gate: {"agrees" or "overrode the draft"} → {route} |
| `review` | you | Waiting for your decision | You {approved / overrode to … / answered} |
| `account_agent_followup` | ai | Looking at the account again with your answer | Account records read again |
| `derive_states_followup` | code | (as derive_states) | (as derive_states) |
| `create_review_case` | code | Filing the review case | Case {case_id} filed under {route} ({"new" or "already existed"}) |

Unknown keys: label = the raw name, owner `code`. The humanised category strings live in the same table (`late_fee_autopay` → "late fee with autopay on", etc.).

### Data flow

```text
browser ──POST /api/threads {account_id, message, label}──▶ server
                                                              │ thread_id = new_thread_id(label); run_id = uuid4()
                                                              │ worker thread: for chunk in graph.stream(state, config, stream_mode=["debug","updates"], subgraphs=True)
                                                              │     for event in narrate(chunk): queue.put(event)
                                                              │ queue.put(pause | result | error)
browser ◀──text/event-stream: data: {json}\n\n ... ───────────┘
browser ──POST /api/threads/{id}/resume {action, route?, reason?, answer?}──▶ same bridge, Command(resume=decision)
```

The server keeps one in-memory dict `run_ids: {thread_id: run_id}` for the trace link. It is lost on restart; the trace endpoint then returns 404 "no trace recorded for this thread in this server session". Accepted.

### UI state (one reducer in `App.tsx`; `Thread.tsx` receives the `thread` slice and dispatches)

```text
{ inbox: InboxItem[], paused: ThreadSummary[],
  selected: InboxItem | ThreadSummary | null,
  thread: { threadId, customerMessage, steps: Step[], pause: ReviewRequest | null,
            result: Result | null, error: string | null, streaming: boolean } | null }
```

A `Step` is `{key, label, owner, detail?, parent?, status: "running" | "done"}`. `start` events append a running step; `end` events mark it done and set `detail`. Sub-steps (`parent: "account_agent"`) render indented under their parent.

### Layout (for the frontend-design pass)

Two columns on a desktop width. Left (320px): "Inbox" header, the queue (customer name, channel label, first line of the message), a "Log a phone call" button that expands into account dropdown + textarea, then "Paused runs". Right: thread header (customer, account, thread ID in monospace), the customer's message as the opening bubble, the step trail (the running line with animated "…", finished lines collapsed under a "{n} steps" toggle that defaults open while streaming and closed once a pause arrives), the pause card with the three actions, the result card. Badges: **AI** (accent), **code** (neutral), **you** (warm). Monospace for every ID. One accent colour. Calm, servicing-desk, not a chatbot toy. Execution loads the `frontend-design` skill before writing Phase B files; its output must still fit the three-source-file inventory and the no-library ban (no splitting into more components to "organise" it).

## 7. API surface

All under `/api`. JSON in, JSON or SSE out. No auth.

| Route | Request | Response |
|---|---|---|
| `GET /api/inbox` | | `{items: [{scenario_id, account_id, customer, channel, message}]}`, dev split only, sorted by `scenario_id` |
| `GET /api/accounts` | | `{items: [{account_id, customer}]}` for the phone-call dropdown |
| `GET /api/threads` | | `{items: [{thread_id, label, account_id, status: "paused" \| "finished", route, updated_at}]}` from the checkpointer, newest first |
| `GET /api/threads/{id}` | | `{status, request?: ReviewRequest, result?: Result}` to reopen a thread (404 if unknown) |
| `POST /api/threads` | `{account_id, message, label?}` (label defaults to `account_id`) | SSE stream (below). 404 unknown account, 422 empty message |
| `POST /api/threads/{id}/resume` | `ReviewDecision` as JSON | SSE stream. 404 unknown thread, 409 finished, 422 invalid decision or `answer` when `can_answer` is false |
| `GET /api/threads/{id}/trace` | | `{url}` or 404 |

### SSE events (the 4 allowed names)

```text
data: {"type":"step","phase":"start","key":"account_agent","label":"Reading the account's records","owner":"ai","parent":null}
data: {"type":"step","phase":"end","key":"tool:list_events","label":"Payment history pulled","owner":"code","parent":"account_agent","detail":null}
data: {"type":"pause","thread_id":"A-1002-20261027T101500Z","request":{...ReviewRequest.model_dump(mode="json")}}
data: {"type":"result","thread_id":"...","case":{...CaseResult},"review":{...ReviewDecision},"draft":{...}|null,"gate":{"route":...,"gate_rejected":...,"gate_reasons":[...],"existing_case_id":...}}
data: {"type":"error","thread_id":"...","message":"..."}
```

Exactly one of `pause`, `result`, `error` ends every stream. `Result` is what the UI needs from `Investigation`, not the whole object (no messages, no retrieved sections).

### Entry point

`python -m app.server [--data data] [--port 8000]` → `uvicorn.run(make_app(...), host="127.0.0.1", port=...)`. Loopback only. The page: `cd app && npm run dev` (Vite on 5173, proxying `/api`).

## 8. Migration plan

None. No schema. The checkpoint file is LangGraph's and is only read. `runs/` stays git-ignored.

## 9. Ordered steps

### Phase A: the event stream (backend)

0. **Spike, 15 minutes, in the scratchpad, not committed.** Using `tests/test_graph.py`'s `run()` fakes, stream the graph with `stream_mode=["debug","updates"], subgraphs=True` and print the chunks. Confirm: (a) `debug` chunks carry `type: "task"` / `"task_result"` with the node `name`; (b) the real `build_account_agent` (not the fake) called via `.invoke` inside `account_node` surfaces its inner `llm_call`/`tool_node`/`finalize` tasks under a namespace. For (b), use a `FakeModel` that emits one tool call then a final answer, or if that takes longer than 15 minutes, use the fake account agent and defer (b) to the first live smoke. **If (b) fails:** stop and ask; the fallback is F1 in §11.
1. **`app/narrate.py` + `tests/test_narrate.py`** (A1). Pure function `narrate(mode, chunk, ns) -> list[Event]` plus the table. Tests feed hand-built debug chunks and the full fake-graph stream; assert the clean sequence, the clarify path labels, the follow-up path labels, the unknown-node fallback, and that `expected_*` never appears in any event.
2. **`app/server.py`**: `make_app`, the six routes, the `__main__` block with `--data`/`--port`, the queue bridge (`threading.Thread` → `queue.Queue` → generator → `StreamingResponse`), thread listing, the 422/409/404 cases copied from `cmd_resume`'s checks. Keep under 250 lines; if it won't fit, stop and ask (budget).
3. **`tests/test_server.py`** (A2) with `TestClient` and the fake graph: inbox shape and exclusions, accounts, a POST that streams to `pause`, a resume with `approve` that streams to `result`, an `answer` on a non-answerable pause → 422, `GET /api/threads` shows the thread as finished, trace → 404 when no LangSmith run.
4. **`requirements.txt`**, run A3 and A6. Commit Phase A.

### Phase B: the page

5. **Scaffold** inside `app/` with `npm create vite@latest . -- --template react-ts` (then delete `App.css`, `index.css`, `src/assets/`, `public/`, `eslint.config.js`, `tsconfig.app.json`/`tsconfig.node.json` if generated, so only the listed files remain; fold the tsconfig into one), set the proxy and the `lint` script, add the two lines to the root `.gitignore`. A4 and A5 pass on the empty app.
6. **`api.ts`**: the types, the SSE reader and the typed calls, mirroring §7 exactly.
7. **Load the `frontend-design` skill**, then write `styles.css`, `App.tsx`, `Thread.tsx` against the §6 layout. A4/A5 after each file.
8. **Paused-runs list and trace link** (W8, W11): reopen a thread from the list at its pause card; "View trace" fetches lazily and renders the link or "trace not available".
9. **Docs**: README, architecture.md, root `.gitignore`.
10. **Manual smoke** (§3, 6 runs max) and the final report: what was run, what the page showed, anything that looked wrong in the agent's behaviour (reported, not fixed). Commit Phase B.

## 10. Tests

| Test | File | Criterion |
|---|---|---|
| `test_clean_sequence`: fake graph stream → start/end pairs in `NORMAL` order, owners as the table says, `review` ends the stream with a `pause` | `tests/test_narrate.py` | A1 |
| `test_clarify_labels`: with `FakeAccountAgent(None-findings, findings)` the trail contains the `account_agent_retry` label once and never twice | `tests/test_narrate.py` | A1 |
| `test_followup_labels`: resume with `answer` on a `need_more_evidence` pause produces the follow-up labels then a second `pause` with `can_answer` false | `tests/test_narrate.py` | A1 |
| `test_unknown_node_falls_back` and `test_no_answer_key_leaks` (grep every event's JSON for `expected_` and `rationale`) | `tests/test_narrate.py` | A1 |
| `test_inbox_dev_only_and_no_key`, `test_accounts`, `test_run_streams_to_pause`, `test_resume_streams_to_result`, `test_answer_refused_422`, `test_threads_list_status`, `test_trace_404_without_run` | `tests/test_server.py` | A2 |
| Existing suite | `tests/` | A3 |
| `npm run build`, `npm run lint` | `app/` | A4, A5 |

Conventions: one test file per source file, fakes from `tests/conftest.py` and `tests/test_graph.py`, `LANGSMITH_TRACING=false` is already forced by the session fixture.

## 11. Edge cases / footguns

- **F1, the one known risk:** if the Account Agent's inner nodes do not surface through `subgraphs=True` because `account_node` calls `.invoke()` without passing the parent config, the fix is a one-line change in `graph.py`'s `account_node`: `account_agent.invoke(initial_state(...), config)` with `config: RunnableConfig` added to the node signature (LangGraph injects it). That touches `graph.py` (not in the inventory) and `tests/test_graph.py`'s `FakeAccountAgent.invoke` already accepts `config=None`. **Stop and ask before doing it**; it is the only approved out-of-inventory change, and it is behaviour-neutral.
- **`debug` chunk shape** differs by LangGraph version (1.2.17 installed): read `chunk["type"]`, `chunk["payload"]["name"]`, `chunk["payload"]["result"]` defensively; unknown shapes are ignored, not raised. The spike pins the exact keys; write them into `narrate.py`'s docstring.
- **`subgraphs=True` changes the chunk tuple** to `(namespace, mode, chunk)`. Namespace `()` is the parent; `("account_agent:<uuid>",)` is the inner graph. The `parent` field is the namespace's first element split on `:`.
- **`__interrupt__` arrives in `updates` mode**, not `debug`. The bridge must watch both.
- **Tool names in `task_result`:** the `tool_node` result is a list of `ToolMessage`s with `.name`; emit one `tool:<name>` start/end pair per message *at result time* (the start event is synthesised just before the end, since the inner node runs all calls in one task). The "…" for the tool shows on `tool_node` start as "Looking things up" until the names arrive. Keep that honest: the label says what is known.
- **Sync checkpointer in a worker thread:** `SqliteSaver` was created with `check_same_thread=False` and has its own lock; one run at a time per server is still the rule (the UI enforces it; the server does not).
- **SSE through the Vite proxy:** set `proxy["/api"].configure` or just confirm the dev proxy does not buffer; if it does, set `Cache-Control: no-cache` and `X-Accel-Buffering: no` headers on the response (harmless, add them anyway).
- **Resume value replay:** a refused `answer` is rejected by the server *before* `Command(resume=...)` is sent (copy `cmd_resume`'s `pending[0]["can_answer"]` check); sending it to the graph re-pauses silently and the UI would show nothing useful.
- **A thread that stopped before `review` (crash):** `GET /api/threads/{id}` reports `paused: false, next: [...]`; show it in the list as "stopped at {node}" with the Continue button; the decision sent is ignored by the graph (documented in `run.py`).
- **`label` for phone-call runs:** use the `account_id`, matching the CLI, so `trace --last` and the thread list read the same.
- **`npm create vite` writes sample files** (`App.css`, `index.css`, `src/assets/`, `public/`, `eslint.config.js`, split tsconfigs): delete them so the inventory stays closed. The lockfile is generated and counted, not written by hand.
- **Do not import `dispute_agent.config` at server-module import for side effects you rely on:** it already runs `load_dotenv()` on import; A6 must pass with no `.env` present, which it will because `make_model` is only called inside `build_graph`.
- **Live smoke spend:** 6 runs, ≈$0.25 each. Stop at 6.

## 12. Open questions

All closed.

## Execution-discipline note

Read the whole plan first. Execute Phase A fully (steps 0–4, all of A1–A3, A6 green) before any Phase B file exists. Re-read §3 Budgets and §4 File inventory before each step; on a budget hit or an out-of-inventory file, stop and ask, except F1 which is pre-approved to *ask about*, not to do. Do not narrow scope silently: if the paused-runs list or the trace link proves hard, leave a `TODO(web): <blocker>` with a compiling stand-in and say so in the report. On a hard single blocker, stand-in + TODO, keep going. Finish with a fresh-context review of both phases against this plan, then the manual smoke, then the report.
