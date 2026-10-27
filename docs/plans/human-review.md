# Plan: Human review inside the graph (pause, resume, recover)

> Written to `docs/plans/` rather than `tasks/`: this repo keeps one plan per slice there (see README "Layout").

## 1. Status

- **Version 1.0, 2026-10-26.** The seventh slice. The v0.6 graph in [pipeline.py](../../dispute_agent/pipeline.py) already defines every node and edge of the orchestrator (classify, the Account and Policy Agents in parallel, derive, the sync join, two bounded re-requests, synthesize, gate, `create_review_case`). What it cannot do is **stop and wait for a person, then pick up where it stopped**, or recover a failed node without starting over. This slice adds that: a **checkpointer** (the graph's state is saved after every node) and **one pause node, `review`**, after the gate, where a human rep approves the route, overrides it with a reason, or, when the route is `need_more_evidence`, answers the draft's open questions so the Account Agent can look again. Brief §5 "Approve: human, then backend" moves inside the graph; the backend stays outside.
- Follows [live-validation.md](live-validation.md) v1.0. **Prerequisite:** that slice's changes to `validate.py`, `pipeline.py` and `__main__.py` are committed before this one starts, because this plan edits all three. Work on a branch `human-review` off `main`.
- **Supersedes** nothing in [orchestrator.md](orchestrator.md); O1–O7 stand. It extends the graph by three nodes and one conditional edge.
- Decided with Rachit on 2026-10-26 (do not re-ask):
  - **Scope: human pause and resume**, not recovery only, not a reshaped graph, not a design doc. Recovery comes for free with the checkpointer and is reported, not built separately.
  - **Two pause points, one node.** The rep approves or overrides before `create_review_case` on every run, and can answer open questions when the route is `need_more_evidence`. Both happen at the same place in the graph, after the gate, so they are one `review` node with one interrupt payload.
  - **The eval never pauses.** `investigate()` auto-approves every pause, so the 12 dev scenarios run unattended and the scores stay comparable to today's 11/12 route.
  - **Checkpoints live in a SQLite file** (`runs/checkpoints.sqlite`, git-ignored), one new dependency. `ask` exits at the pause; `resume <thread>` continues in a new process. LangSmith shows the two runs on one thread.
- **Built 2026-10-26, with two corrections recorded in [architecture.md](../architecture.md) v0.7 (H section):** a refused answer re-pauses inside `review` instead of raising `ValueError` (LangGraph replays a stored resume value, so a raise would never clear), and the checkpoint serializer lists our state models explicitly. Work went on `main`, not a branch. The `ValueError` text below is the plan as written.
- Plain words for the two new terms, for the docs: a **checkpointer** is a save-game file for the graph's one shared state object, written after every node; an **interrupt** is a pause point where the graph stops, hands the caller a payload, and later continues from the same save with the person's answer.

## 2. Why & goal

**Context.** Today a run is one `invoke`: eleven nodes, no stop, and the rep sees the result only after `create_review_case` has "written". The brief promises "a human decides", and the architecture doc's step 5 says so, but that step is outside the code. The video Rachit shared (LangGraph's own pitch) names exactly this as the point of shared state: pause for a person or another system, resume where it stopped, recover a failed agent at step 50 instead of step 1.

**The bet.** Adding the pause changes no decision the agents or the gate make, so the dev eval's route accuracy stays at 11/12 with auto-approve; and the answer path turns S-15 (two refund requests, the customer did not say which) from `need_more_evidence` into a decided route once the rep says which refund, with the Account Agent re-run bounded to one. If the eval moves, the pause leaked into a decision, and that is a bug.

**Use case:**

```bash
.venv/bin/python -m dispute_agent ask A-1016 "where is my refund?? been weeks"
# ...prints category, findings, derived, policy, sync, draft and gate as today, then:
# paused for review: thread A-1016-20261027T101500Z
#   route need_more_evidence (gate ok)  open questions: Which refund: the $25.00 on 09-30 or the $731.50 on 10-07?
#   resume with:  resume A-1016-20261027T101500Z --approve
#                 resume A-1016-20261027T101500Z --route adjustment_review --reason "..."
#                 resume A-1016-20261027T101500Z --answer "..."
.venv/bin/python -m dispute_agent resume A-1016-20261027T101500Z --answer "customer means the $731.50 refund"
# ...the Account Agent runs again with the answer, derive, sync, synthesize and gate rerun, then:
# paused for review: thread A-1016-20261027T101500Z  (answers used: 1 of 1)
#   route adjustment_review (gate ok)
.venv/bin/python -m dispute_agent resume A-1016-20261027T101500Z --approve
# create_review_case (code): C-004 created=True route=adjustment_review  decided by: rep (approved)
```

In LangSmith: three root runs on one thread, `dispute A-1016`, `dispute A-1016 resume`, `dispute A-1016 resume`; the first ends at `review`, the second starts at `review` and ends at `review`, the third is `review > create_review_case`.

**This plan covers:** the checkpointer, the `review` node and its payload, the `account_agent_followup` and `derive_states_followup` nodes, `start`/`resume` in `pipeline.py`, the `resume` CLI command and `ask --approve`, the rep's override in `create_review_case`, the trace check's new shapes, tests, docs and the diagram. Nothing else.

## 3. Definition of Done

### Acceptance criteria

```bash
# A1 offline tests, whole suite (today's count + new), no LLM, no network
.venv/bin/python -m pytest tests -q
# A2 size fence
[ "$(wc -l < dispute_agent/pipeline.py)" -le 270 ] && [ "$(wc -l < dispute_agent/review.py)" -le 120 ] \
  && [ "$(wc -l < dispute_agent/validate.py)" -le 190 ] && [ "$(wc -l < tests/test_review.py)" -le 200 ] \
  && [ "$(wc -l < tests/test_pipeline.py)" -le 220 ]
# A3 live approve path: ask pauses, resume in a new process finishes, both traced
set -a; . ./.env; set +a
T=$(.venv/bin/python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues, conf # ends in 8812" --quiet | sed -n 's/^paused for review: thread //p')
[ -n "$T" ] || exit 1
.venv/bin/python -m dispute_agent resume "$T" --approve | grep -q "create_review_case (code): C-004 created=True route=adjustment_review"
.venv/bin/python -m dispute_agent trace --last 1
# A4 live answer path on S-15's account (dev): pause, answer, pause again, approve
T=$(.venv/bin/python -m dispute_agent ask A-1016 "where is my refund?? been weeks" --quiet | sed -n 's/^paused for review: thread //p')
.venv/bin/python -m dispute_agent resume "$T" --answer "customer means the \$731.50 refund requested Oct 7" | grep -q "paused for review"
.venv/bin/python -m dispute_agent resume "$T" --approve | grep -q "create_review_case (code)"
.venv/bin/python -m dispute_agent trace --last 2
# A5 ask --approve runs to the end in one process (what the ad-hoc script and A3/A4 of live-validation need)
.venv/bin/python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues, conf # ends in 8812" --quiet --approve | grep -q "create_review_case (code)"
# A6 the dev eval still passes its thresholds with auto-approve; route stays 11/12 or better; no pause reaches the console
.venv/bin/python -m dispute_agent eval --min-disputed-acc 0.75 --min-policy-recall 0.75 --min-route-acc 0.75 | grep -qv "paused for review"
# A7 resume on an unknown or finished thread exits 2 and runs no model
.venv/bin/python -m dispute_agent resume no-such-thread --approve; [ $? -eq 2 ]
# A8 only inventory files changed
git status --porcelain
```

- **If A6 drops below 11/12 route:** stop. The pause must not change any decision; find the leak (most likely the `rep_message` rewrite or the attempts counters), fix it, rerun. No prompt change is available in this plan.
- **A4's answer is scored by hand** in the architecture doc's example: after the answer, `derived.fact_ambiguous` is false and `disputed_event_id` is the $731.50 request. If the Account Agent still returns both candidates, that is a finding about the agent, reported, not fixed here.
- **Held-out scenarios (S-04, S-08, S-12, S-16) are never run.**

### Budgets

| Budget | Max |
|---|---|
| New files | 4: `dispute_agent/review.py`, `tests/test_review.py`, `docs/diagrams/architecture_v0.7.png`, this plan |
| LOC per code file | 270 `pipeline.py` (up from 220: three nodes, one edge, `start`/`resume`), 120 `review.py`, 190 `validate.py`, 200 `tests/test_review.py`, 220 `tests/test_pipeline.py` |
| New dependencies | 1: `langgraph-checkpoint-sqlite` |
| New CLI commands / flags | 1 command (`resume`); flags `--approve` on `ask`; `--approve`, `--route`, `--reason`, `--answer` on `resume` |
| New graph nodes / conditional edges | 3 nodes (`review`, `account_agent_followup`, `derive_states_followup`), 1 conditional edge (`after_review`) |
| New `GraphState` fields | 3: `review`, `followups`, `rep_answer` |
| New `config.py` constants | 0 (`MAX_FOLLOWUPS = 1` sits next to `MAX_ATTEMPTS` in `pipeline.py`: graph shape) |
| New environment variables | 0 |
| New files on disk at runtime | 1: `runs/checkpoints.sqlite` (git-ignored with the rest of `runs/`) |
| New spreadsheet columns / data changes | 0 |
| New eval checks | 0 (`trajectory_ok` and `action_ok` keep their formulas; the JSON row gains `review` and `followups` for inspection only) |
| Prompt changes | 0 |
| LLM calls per scenario on the eval path | unchanged |
| Extra LLM calls on the answer path | one Account Agent run (loop + finalize) and one synthesize; plus at most the existing bounded re-requests from sync |
| Model spend | $3 for this plan (A3 ≈ $0.06, A4 ≈ $0.15, A5 ≈ $0.06, A6 ≈ $0.75; headroom for one rerun each) |

### Must-handle edge cases

- **`resume` on a thread with no checkpoint:** prints `no paused run for thread X` and exits 2, before any model is built.
- **`resume` on a finished thread** (state has `case`, nothing pending): prints `thread X already finished: case C-00N` and exits 2.
- **`--answer` when the pause did not allow one** (`can_answer` false: route is not `need_more_evidence`, or the one follow-up is spent): the CLI prints `this pause takes --approve or --route only` and exits 2 without resuming. The node guards too: an `answer` decision with `can_answer` false raises `ValueError`, which leaves the thread paused and resumable.
- **`--route` without `--reason`, or an unknown route:** argparse error, exit 2.
- **Override to the same route the gate chose:** recorded as `override` with the reason anyway; the case gets that route. No special case.
- **Gate rejected the draft** (route forced to `manual_escalation`): `review` still pauses; the payload carries `gate_rejected` and the reasons; `can_answer` is false.
- **Synthesize failed** (`draft` is None): the payload has `draft: null` and empty `open_questions`; approve or override only; the case is filed under the gate's `manual_escalation`.
- **After an answer, the Account Agent's finalize fails:** `account_attempts` is already at least 1 before the follow-up, so sync's clarify may fire once if `account_attempts < MAX_ATTEMPTS` after the follow-up increments it, and never otherwise. Either way the run reaches `review` again; the second pause never offers an answer.
- **After an answer, the disputed event changes and so does `policy_date`:** sync's `policy_date_mismatch` may route to `policy_agent_retry` if `policy_attempts < MAX_ATTEMPTS`. Existing bound, unchanged.
- **The eval:** every pause is auto-approved inside `investigate()`; every row has `review.action == "approve"` and `followups == 0`; `trajectory_ok` and `action_ok` are unchanged. The eval's graph uses an in-memory checkpointer, so `runs/checkpoints.sqlite` is never touched by `eval`.
- **Parallel nodes and SQLite:** LangGraph runs the fan-out on worker threads; the connection is opened with `check_same_thread=False` (the saver serialises access with its own lock). A test runs the full fan-out through a file-backed saver.
- **Resume in a new process:** the graph is rebuilt from the same data; the thread's state comes from the file. State holds pydantic models, LangChain messages and datetimes; the default serializer round-trips them. A test proves it: start with one saver on a temp file, drop it, open a second saver on the same file, resume, get an `Investigation`.
- **The `review` node re-runs from its top on resume** (LangGraph semantics). It does nothing before `interrupt()` except build the payload from state, so re-running is free. The write stays in `create_review_case`, a separate node, so it runs exactly once.
- **`DISPUTE_FAULT` with resume:** each process reads the variable at build time. `finalize_once` keys on `account_attempts == 0`, so it never fires on a follow-up. Unset is identical to today.
- **Recovery:** if a node raises mid-run (API error) with the SQLite saver, `resume <thread>` re-invokes from the last checkpoint. No new code; one test asserts that `start` after a failing node raises, and `resume` on that thread completes once the fake stops failing. Documented, not promised beyond that test.

### Out of scope

- A web UI, streaming to a client, an approval queue, authentication, or an audit log. The rep is the person at the terminal.
- Editing the case note or the cited IDs at the pause. Override is a route plus a reason; the note stays the agent's.
- Changing what the agents, sync, synthesize or the gate decide; any prompt change; any change to `MAX_ATTEMPTS`.
- Scoring the answer path in `eval` (no dataset of rep answers exists; it is a live demo, scored by hand once).
- Encrypted checkpoints, checkpoint pruning, Postgres.
- **Forbidden by name:** no LLM in `review` (the rep decides, code records); no second interrupt node; no `Command(goto=...)` routing from inside a node (the conditional edge `after_review` stays visible in the graph and the diagram); no new config file or env var for the checkpoint path (it is a constant in `__main__.py`); no new Pydantic model beyond `ReviewRequest`, `ReviewDecision` and `Pause`; no change to `GateResult`, `Draft`, `SyncResult`, `CaseResult` fields; no retry-on-error loop inside the graph; no `langsmith` client call outside `validate.py`; no test that calls a real model.

## 4. File inventory (closed)

| Path | Action | Owns |
|---|---|---|
| `dispute_agent/review.py` | CREATE | `ReviewRequest`, `ReviewDecision`, `Pause`; `build_request(state) -> ReviewRequest` (code renders the payload); `with_answer(rep_message, answer) -> str`; `APPROVE` (the auto-approve decision) |
| `dispute_agent/pipeline.py` | UPDATE | `MAX_FOLLOWUPS`; `GraphState` + 3 fields; `review_node`, `after_review`, `account_followup_node` (wraps `account_node`), `derive_states_followup`; `build_graph(..., checkpointer=None)`; `sqlite_checkpointer(path)`; `start`, `resume`, `_outcome`; `investigate` auto-approve loop; `Investigation` + `review`, `followups`, `thread_id` |
| `dispute_agent/create_review_case.py` | UPDATE | `route_override: Route | None = None` parameter; docstring |
| `dispute_agent/__main__.py` | UPDATE | `CHECKPOINTS = Path("runs") / "checkpoints.sqlite"`; `cmd_ask` prints the pause or, with `--approve`, the case; `cmd_resume`; `_print_outcome` shared by both; argparse for `resume` and `ask --approve`; docstring |
| `dispute_agent/evaluate.py` | UPDATE | row gains `"review"` and `"followups"`; nothing else |
| `dispute_agent/validate.py` | UPDATE | `NORMAL` ends with `review`; `expected_children` unchanged otherwise; `expected_resume_children(followups, policy_retry)`; `trace_check` branches on root names ending in ` resume` |
| `tests/test_pipeline.py` | UPDATE | `run()` passes a thread config and auto-resumes with approve; `NORMAL` gains `review`; existing assertions keep their meaning |
| `tests/test_review.py` | CREATE | payload, approve, override, answer path, answer refused, unknown/finished thread, SQLite cross-saver resume, fan-out through SQLite, recovery (§10) |
| `tests/test_create_review_case.py` | UPDATE | override test |
| `tests/test_evaluate.py` | UPDATE | row has `review.action == "approve"`, `followups == 0` |
| `tests/test_validate.py` | UPDATE | `review` in the normal shape; resume shapes |
| `requirements.txt` | UPDATE | `langgraph-checkpoint-sqlite>=3,<4` (pin to the installed major after `pip install`; record the exact version in the commit message) |
| `README.md` | UPDATE | "Run it": `ask` pauses, `resume`, `--approve`; the step → file table gains `review` |
| `docs/architecture.md` | UPDATE | v0.7: the graph section gains `review` and the follow-up nodes; §5 "Approve" becomes "Review, in the graph"; "Graph state" table + 3 fields; decision H1–H8 rows; version history |
| `docs/project_brief.md` | UPDATE | v0.9 note; §4 one sentence on the pause; §8 Boundaries unchanged in meaning |
| `docs/diagrams/architecture_v0.7.png` | CREATE | redraw with the `review` pause (dashed "rep" lane) and the follow-up edge; v0.6 PNG is `git rm`'d; HTML source stays in the session scratchpad (diagram-style rule) |
| `docs/plans/human-review.md` | CREATE | this plan |

**Touching any other file is a scope breach: stop and ask.** In particular `account_agent.py`, `policy_agent.py`, `sync.py`, `synthesize.py`, `gate.py`, `derive.py`, `config.py`, `data/` and `docs/plans/live-validation-adhoc.sh` are not in the inventory. (The ad-hoc script keeps working: the sync line it greps prints before the pause. If it is re-run after this slice and someone wants the case line, add `--approve` to its eight lines then, as its own one-line change.)

**Read first:**
- [pipeline.py](../../dispute_agent/pipeline.py) all of it: `GraphState`, `Investigation.from_state`, `account_node` (the fault wrapper), `after_sync`, `build_graph`'s node and edge list, `investigate`.
- [create_review_case.py](../../dispute_agent/create_review_case.py) all 43 lines.
- [__main__.py](../../dispute_agent/__main__.py) `cmd_ask` lines 95–150 (what it prints, in order) and `main()`.
- [validate.py](../../dispute_agent/validate.py) `NORMAL`, `expected_children`, `_children_match`, `trace_check` lines 60–115.
- [tests/test_pipeline.py](../../tests/test_pipeline.py) `FakeAccountAgent`, `run()`, `NORMAL`, `test_clarify_once` (the order-assert pattern).
- [gate.py](../../dispute_agent/gate.py) lines 198–224 (`Draft`, `GateResult`): the payload's source fields.
- [orchestrator.md](orchestrator.md) §5 O3 (why retry nodes are separate nodes: the join barrier) and §6.

## 5. Closed decisions (do not re-litigate)

| # | Decision | Why this, not X |
|---|---|---|
| H1 | **One pause node, `review`, after the gate; the payload says whether an answer is allowed** | Both pause points Rachit chose happen at the same moment (the rep has the draft and the route). Two interrupt nodes would mean two places to resume and a second conditional edge for nothing |
| H2 | **Three rep actions: `approve`, `override` (route + reason), `answer` (text)**; `answer` only when the route is `need_more_evidence` and no follow-up has been used | The route is the action (D8), so overriding it is the whole power a rep has; an answer is the one input that can change the facts. Editing the note or the citations is out of scope |
| H3 | **The answer is folded into `rep_message`** as `"<original>\n\nRep follow-up (after review): <answer>"`, and `rep_answer` keeps the raw text | Every downstream node (the Account Agent, a clarify retry, synthesize's rendering) already reads `rep_message`; no prompt or renderer changes. The label makes the trace honest |
| H4 | **The follow-up is its own node pair, `account_agent_followup` → `derive_states_followup` → `sync`**, same functions as the retry nodes, one bound `MAX_FOLLOWUPS = 1` | O3: re-entering `account_agent` or `derive_states` hits the join barrier. Separate names make the trace read as what happened. `account_attempts` still counts the run, so sync's clarify bound stays honest |
| H5 | **`build_graph` always compiles with a checkpointer; the default is `InMemorySaver()`** | `interrupt()` needs one. Tests and `eval` get it for free; the CLI passes the SQLite saver. One graph, one shape, no "pause off" switch |
| H6 | **`investigate()` keeps its signature and auto-approves** every pause; `start`/`resume` are the two new entry points the CLI uses | `evaluate.py` changes by two row keys; the eval measures the same decisions as today. The CLI is the only place a person is in the loop |
| H7 | **Thread IDs are `<label>-<UTC timestamp>`**, minted by the caller, printed by `ask` | A rep can read it; two runs on the same account never collide; `eval` uses the scenario ID as the label |
| H8 | **The override goes into `create_review_case` as a parameter, not into `GateResult`** | The gate's result stays what the gate decided; the case's `route` is the final route, as today; `Investigation.review` says who decided |
| H9 | **Resume root runs are named `dispute <label> resume`** and carry the original tags plus `resume` | The trace check can tell them apart by name; LangSmith groups them by `thread_id` in metadata |
| H10 | **`trace_check` treats an approve-only resume root as invisible** (zero prompt tokens, already filtered) and checks answer-path resume roots against their own shapes | The existing zero-token filter does the right thing; only the roots with model calls are worth a shape check |

## 6. Architecture / schema

### Code vs model split

No new model step. The only model call the slice can add is the existing Account Agent run (and synthesize) on the answer path, with the rep's text as extra input.

| Output field | Where it comes from | Owner |
|---|---|---|
| `ReviewRequest.*` (route, gate flags, draft, open questions, `can_answer`, counters) | `GraphState`: `gate`, `synthesis.draft`, `followups`, attempts | code |
| `ReviewDecision` | the rep, via the CLI (or `APPROVE` in `investigate`) | the rep (code validates) |
| `rep_message` after an answer | `with_answer(original, answer)` | code |
| `account` after a follow-up | the Account Agent run, as today | model (existing extraction, validated by `derive_states`) |
| `CaseResult.route` | `review.route` when action is `override`, else `gate.route` | code |
| `followups`, `thread_id` | counters and the caller | code |
| checkpoint rows | LangGraph's saver | code |

### The graph (v0.7)

```
START → classify ─┬→ account_agent → derive_states ─┐
                  └→ policy_agent ───────────────────┴→ sync ─┬→ account_agent_retry → derive_states_retry → sync
                                                             ├→ policy_agent_retry → sync
                                                             └→ synthesize → gate → review ─┬→ create_review_case → END
                                                                                            └→ account_agent_followup → derive_states_followup → sync
```

Node list, with the owner and what each reads and writes. Existing nodes are unchanged unless marked.

| Node | Owner | Reads | Writes | New |
|---|---|---|---|---|
| `classify` | LLM (1 call) | `rep_message` | `category` | |
| `account_agent` | Account Agent (LLM + tools) | `account_id`, `category`, `rep_message` | `account`, `account_attempts` | |
| `policy_agent` | Policy Agent (retrieval + 1 call) | `category`, `rep_message` | `policy`, `policy_attempts` | |
| `derive_states` | code | `account.findings`, `category` | `derived` | |
| `sync` (join) | code | `account`, `policy`, `derived` | `sync` | |
| `account_agent_retry`, `derive_states_retry` | as above | | | |
| `policy_agent_retry` | Policy Agent | + `derived`, `account.findings` | `policy`, `policy_attempts` | |
| `synthesize` | LLM (1 call) | everything above | `synthesis` | |
| `gate` | code | `synthesis.draft`, `derived`, `policy` | `gate` | |
| **`review`** | **human (code renders, code validates)** | `gate`, `synthesis`, `followups`, attempts | `review` | ✓ |
| **`account_agent_followup`** | Account Agent | `rep_message` (rewritten), `review.answer` | `account`, `account_attempts`, `rep_message`, `rep_answer`, `followups` | ✓ |
| **`derive_states_followup`** | code | as `derive_states` | `derived` | ✓ |
| `create_review_case` | code (the only write) | `gate`, `derived`, `review` | `case` | override param |

Edges: as today plus `gate → review` (replaces `gate → create_review_case`), `review → after_review`, `account_agent_followup → derive_states_followup → sync`.

```python
MAX_FOLLOWUPS = 1

def after_review(s: GraphState) -> Literal["account_agent_followup", "create_review_case"]:
    """The second decision in the graph: the rep answered, so look again once; otherwise file the case."""
    return "account_agent_followup" if s["review"].action == "answer" else "create_review_case"
```

`after_sync` is unchanged. After a follow-up, `sync` may still route to `account_agent_retry` (if the follow-up's finalize failed and `account_attempts < MAX_ATTEMPTS`) or `policy_agent_retry` (if the new `policy_date` has a different in-force set and `policy_attempts < MAX_ATTEMPTS`); both bounds are the existing ones.

### `review.py`

```python
Action = Literal["approve", "override", "answer"]

class ReviewRequest(BaseModel):
    """What the rep sees at the pause. JSON-serializable: it is the interrupt payload."""
    thread_label: str
    route: Route                      # gate.route
    gate_rejected: bool
    gate_reasons: list[str]
    existing_case_id: str | None
    draft: dict | None                # synthesis.draft.model_dump(), or None when synthesize failed
    open_questions: list[str]
    can_answer: bool                  # route == "need_more_evidence" and followups < MAX_FOLLOWUPS
    account_attempts: int
    policy_attempts: int
    followups: int

class ReviewDecision(BaseModel):
    action: Action
    route: Route | None = None        # override only
    reason: str | None = None         # override only, required
    answer: str | None = None         # answer only, required

    @model_validator(mode="after")
    def _shape(self): ...             # override needs route+reason; answer needs answer; approve needs nothing

class Pause(BaseModel):
    thread_id: str
    request: ReviewRequest

APPROVE = ReviewDecision(action="approve")

def build_request(s: GraphState, label: str, max_followups: int) -> ReviewRequest: ...
def with_answer(rep_message: str, answer: str) -> str:
    return f"{rep_message}\n\nRep follow-up (after review): {answer}"
```

### `pipeline.py` additions

```python
class GraphState(TypedDict, total=False):
    ...  # today's fields
    review: ReviewDecision | None
    followups: int
    rep_answer: str | None

def review_node(s):                       # nothing before interrupt() but reading state
    request = build_request(s, s["thread_label"], MAX_FOLLOWUPS)   # thread_label comes in with the initial state
    raw = interrupt(request.model_dump(mode="json"))
    decision = ReviewDecision.model_validate(raw)
    if decision.action == "answer" and not request.can_answer:
        raise ValueError("this pause does not take an answer")   # the thread stays paused
    return {"review": decision}

def account_followup_node(s):
    message = with_answer(s["rep_message"], s["review"].answer)
    out = account_node({**s, "rep_message": message})            # same wrapper, same fault rules
    return {**out, "rep_message": message, "rep_answer": s["review"].answer, "followups": s["followups"] + 1}

def case_node(s):
    override = s["review"].route if s["review"].action == "override" else None
    return {"case": create_review_case(ds, s["account_id"], s["category"], s["derived"], s["gate"], override)}

def build_graph(..., checkpointer=None):
    ...
    g.add_node("review", review_node)
    g.add_node("account_agent_followup", account_followup_node)
    g.add_node("derive_states_followup", derive_node)
    g.add_edge("gate", "review")
    g.add_conditional_edges("review", after_review, ["account_agent_followup", "create_review_case"])
    g.add_edge("account_agent_followup", "derive_states_followup")
    g.add_edge("derive_states_followup", "sync")
    g.add_edge("create_review_case", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())

def sqlite_checkpointer(path: Path) -> SqliteSaver:
    path.parent.mkdir(exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))

def _config(thread_id, tags, run_name): return {"configurable": {"thread_id": thread_id}, "tags": tags, "run_name": run_name}

def _outcome(final, thread_id) -> Investigation | Pause:
    if "__interrupt__" in final:
        return Pause(thread_id=thread_id, request=ReviewRequest.model_validate(final["__interrupt__"][0].value))
    return Investigation.from_state(final, thread_id)

def start(graph, account_id, rep_message, thread_id, tags, label) -> Investigation | Pause:
    final = graph.invoke(initial_graph_state(account_id, rep_message, label), _config(thread_id, tags, f"dispute {label}"))
    return _outcome(final, thread_id)

def resume(graph, thread_id, decision, tags, label) -> Investigation | Pause:
    final = graph.invoke(Command(resume=decision.model_dump()), _config(thread_id, [*tags, "resume"], f"dispute {label} resume"))
    return _outcome(final, thread_id)

def thread_status(graph, thread_id) -> Literal["paused", "finished", "unknown"]:
    snap = graph.get_state({"configurable": {"thread_id": thread_id}})
    if not snap.values: return "unknown"
    return "paused" if snap.next else "finished"

def investigate(ds, sections, account_agent, account_id, rep_message, tags, label, graph=None) -> Investigation:
    """Unattended: run the graph and approve every pause. What eval uses."""
    graph = graph or build_graph(ds, sections, account_agent)
    thread_id = f"{label}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"
    out = start(graph, account_id, rep_message, thread_id, tags, label)
    while isinstance(out, Pause):
        out = resume(graph, thread_id, APPROVE, tags, label)
    return out
```

`initial_graph_state` gains `thread_label` (the label, so `review` can show it) and `followups: 0`, `review: None`, `rep_answer: None`. `Investigation` gains `review: ReviewDecision`, `followups: int`, `thread_id: str`. Nodes are closures over `ds`, `sections` and the models, so a resume process rebuilds the graph from the same data; only state comes from the file.

### `__main__.py`

- `CHECKPOINTS = Path("runs") / "checkpoints.sqlite"`.
- `ask`: builds the graph with `sqlite_checkpointer(CHECKPOINTS)`, mints `thread_id = f"{account_id}-{now:%Y%m%dT%H%M%SZ}"`, calls `start`. Prints what it prints today through the gate section (it reads those from the graph's state snapshot, `graph.get_state(config).values`, the same fields `Investigation` reads). Then either the pause block (above) or, with `--approve`, resumes with `APPROVE` and prints the case line. The `sync (code)` line keeps its exact text; `live-validation-adhoc.sh` greps it.
- `resume <thread_id> (--approve | --route R --reason "..." | --answer "...")`: mutually exclusive group, `--route` requires `--reason`. Checks `thread_status`: `unknown` → exit 2, `finished` → exit 2 with the case ID. Reads the pending payload from `graph.get_state(config).tasks[0].interrupts[0].value` to validate `--answer` against `can_answer` before resuming. Then `resume(...)` and prints: on a `Pause`, the pause block; on an `Investigation`, the `create_review_case` line plus `decided by: rep (approved | override to X: reason)`, and when a follow-up ran, the follow-up's findings, derived, sync and draft sections in the same format as `ask`.
- `eval` is unchanged on the surface (it goes through `investigate`).

### `validate.py`

```python
NORMAL = [..., "synthesize", "gate", "review"]            # the first root ends at the pause
RESUME_HEAD = ["review", "account_agent_followup", "derive_states_followup", "sync"]
RESUME_TAIL = ["synthesize", "gate", "review"]

def expected_resume_children(policy_retry: bool) -> list[str]:
    return RESUME_HEAD + (["policy_agent_retry", "sync"] if policy_retry else []) + RESUME_TAIL
```

`trace_check`: a root whose name ends with ` resume` and has prompt tokens is an answer-path run; it matches if its children equal `expected_resume_children(False)` or `expected_resume_children(True)` (the root's outputs cannot say in which root the re-retrieve happened). A resume root with zero prompt tokens (approve or override) is already filtered out and never checked. The `_children_match` swap rule applies only to the first root.

### Checkpoint storage

One SQLite file, `runs/checkpoints.sqlite`, created on first `ask`. Tables are LangGraph's own (`checkpoints`, `writes`); nothing of ours. It grows with every run; `runs/` is git-ignored and the README says it can be deleted at any time (deleting it forgets paused threads).

### LangSmith

Root run per `invoke`: `dispute <label>` (ends at `review`), `dispute <label> resume` (one per resume). `thread_id` is in each run's `configurable` metadata, so the three runs of one complaint are one thread in the LangSmith UI. Child names are node names, as today.

## 7. API surface

| Surface | Change |
|---|---|
| `pipeline.build_graph(..., checkpointer=None)` | new kwarg; always compiles with a checkpointer |
| `pipeline.start`, `pipeline.resume`, `pipeline.thread_status`, `pipeline.sqlite_checkpointer` | new |
| `pipeline.investigate(...)` | same signature; auto-approves; returns `Investigation` |
| `pipeline.Investigation` | + `review`, `followups`, `thread_id` |
| `pipeline.initial_graph_state(account_id, rep_message, label)` | + `label` |
| `review.ReviewRequest`, `review.ReviewDecision`, `review.Pause`, `review.APPROVE`, `review.build_request`, `review.with_answer` | new |
| `create_review_case(ds, account_id, category, derived, verdict, route_override=None)` | new optional parameter |
| CLI `ask ... [--approve]` | pauses by default |
| CLI `resume <thread_id> (--approve \| --route R --reason S \| --answer S)` | new |
| `evaluate` JSON row | + `review`, `followups` |
| `validate.NORMAL`, `validate.expected_resume_children` | changed / new |

## 8. Migration plan

- No data or schema migration. `pip install langgraph-checkpoint-sqlite` and the `requirements.txt` pin.
- `runs/eval_*.json` written before this slice lack `review` and `followups`; `stability` reads only `checks`, so old reports still compare.
- Down: remove the `review` edges and the kwarg; `interrupt` is the only call that needs the checkpointer. The SQLite file can be deleted.

## 9. Ordered steps

Each step ends with `pytest tests -q` green. Re-read §3 Budgets and §4 File inventory before each step.

1. **Branch and dependency.** `git checkout -b human-review` (after live-validation is committed). `pip install langgraph-checkpoint-sqlite`, pin it in `requirements.txt`. Spike in the scratchpad, not the repo: a three-node graph with `interrupt`, `SqliteSaver` on a temp file with `check_same_thread=False`, one parallel fan-out, resume in a second saver instance. Confirms the API on langgraph 1.2.17 before any repo code. Ten minutes; throw it away.
2. **`review.py`** with the three models, `APPROVE`, `build_request`, `with_answer`. Tests: validator shapes, `can_answer` truth table (route × followups), `with_answer` text.
3. **The graph.** `GraphState` fields, `initial_graph_state(label)`, `review_node`, `after_review`, the follow-up nodes, the edges, `build_graph(checkpointer)`, `sqlite_checkpointer`, `start`, `resume`, `thread_status`, `_outcome`, `investigate` loop, `Investigation` fields. Update `tests/test_pipeline.py`'s `run()` to pass a thread config and auto-resume; `NORMAL` gains `review`. All existing pipeline tests pass with the new order.
4. **`create_review_case` override** and its test.
5. **`tests/test_review.py`**: the pause payload on S-02 and S-15, approve, override, the answer path on S-15 (fake agent returns ambiguous findings first, the $731.50 pick second), answer refused, SQLite cross-saver resume on `tmp_path`, fan-out through SQLite, recovery, `investigate` auto-approve.
6. **CLI.** `ask` pause block and `--approve`; `resume`; `_print_outcome`. Run A5 and A7 (A7 needs no `.env`).
7. **`evaluate.py` row keys and `validate.py` shapes**, with their tests.
8. **Live.** A3, A4, then A6 (one dev eval, ≈ $0.75). Record the three `runs/eval_*.json` file name and the LangSmith thread IDs in the architecture doc's version note.
9. **Docs and diagram.** `README.md`, `architecture.md` v0.7 (graph, state table, H1–H10, the A4 example with the actual output), brief v0.9, `architecture_v0.7.png` rendered from scratchpad HTML; `git rm` the v0.6 PNG.
10. **Fresh-context review** of the diff against this plan (a subagent given the plan and the diff, not this conversation), then A1–A8 once more, then commit on the branch.

## 10. Tests

All offline, fakes only, `LANGSMITH_TRACING=false` from `conftest.py`.

| Test | File | Criterion |
|---|---|---|
| `test_decision_shapes` (override needs route+reason, answer needs text, approve bare) | `test_review.py` | A1 |
| `test_can_answer_truth_table` | `test_review.py` | A1 |
| `test_pause_payload_s02` (route adjustment_review, `can_answer` False, draft present) | `test_review.py` | A1 |
| `test_pause_payload_s15` (`can_answer` True, open questions non-empty) | `test_review.py` | A1 |
| `test_approve_files_gate_route` (order `NORMAL + [create_review_case]`, case route = gate route, `review.action == "approve"`) | `test_review.py` | A1, A3 |
| `test_override_files_rep_route` (case route = override, reason kept, gate result untouched) | `test_review.py` | A1 |
| `test_answer_reruns_account_agent_once` (S-15: order `NORMAL + RESUME_HEAD + RESUME_TAIL + [create_review_case]`, `followups == 1`, `account_attempts == 2`, second pause `can_answer` False, `rep_message` carries the follow-up line, `rep_answer` set) | `test_review.py` | A1, A4 |
| `test_answer_refused_when_not_allowed` (`ValueError`; `thread_status` still `paused`; approve then completes) | `test_review.py` | A1 |
| `test_answer_then_clarify_bound` (follow-up finalize fails: clarify fires only if attempts allow; run reaches review) | `test_review.py` | A1 |
| `test_sqlite_resume_across_savers` (`tmp_path` file, two `SqliteSaver` instances, `Investigation` returned, pydantic and messages round-trip) | `test_review.py` | A1, A3 |
| `test_sqlite_fan_out` (full graph through a file saver, order unchanged) | `test_review.py` | A1 |
| `test_recovery_resumes_from_checkpoint` (fake synthesize model raises once; `start` raises; `resume` completes with the same thread) | `test_review.py` | A1 |
| `test_thread_status_unknown_finished` | `test_review.py` | A1, A7 |
| `test_investigate_auto_approves` (`followups == 0`, `review.action == "approve"`) | `test_review.py` | A1, A6 |
| existing `test_graph_clean`, `test_clarify_once`, `test_re_retrieve_once`, `test_both_re_requests_then_stop`, fault tests: orders gain `review` | `test_pipeline.py` | A1 |
| `test_override_param` | `test_create_review_case.py` | A1 |
| `test_row_has_review` | `test_evaluate.py` | A1, A6 |
| `test_expected_children_ends_with_review`, `test_resume_shapes`, `test_trace_check_resume_root` | `test_validate.py` | A1, A3, A4 |

## 11. Edge cases / footguns

- **Anything before `interrupt()` in `review_node` runs twice.** Keep it to reading state. No printing, no counters, no model calls there.
- **`Command(resume=...)` must be the input of `invoke`, and `thread_id` must match.** A resume with a fresh `thread_id` starts a new run with `Command` as input and fails confusingly. `resume` in `pipeline.py` never mints IDs.
- **The join barrier.** `account_agent_followup` must not be the `account_agent` node, and `derive_states_followup` must not be `derive_states`; O3's spike showed a silent end of run otherwise. Same rule as the retry nodes.
- **`__interrupt__` key.** `graph.invoke` returns the state plus `__interrupt__` (a tuple of `Interrupt`, `.value` is the payload) when paused. `Investigation.from_state` must not be called on a paused state; `_outcome` checks the key first.
- **`check_same_thread=False`** on the SQLite connection or the fan-out worker threads raise `ProgrammingError`.
- **The interrupt payload must be JSON-serializable.** Pass `model_dump(mode="json")`, never the pydantic object; datetimes become strings.
- **`get_state(...).tasks[0].interrupts`** is where the pending payload lives for `resume`'s pre-check; `.next` is non-empty while paused. A thread with no checkpoint returns an empty `values`.
- **`ask` must read its printout from the state snapshot**, not from an `Investigation` (there is none yet at the pause). Both the pause path and the `--approve` path print the same sections, so write one `_print_investigation_sections(values)` and call it in both.
- **`account_attempts` after a follow-up is at least 2**, so `trajectory_ok`'s `<= MAX_ATTEMPTS` would fail on a CLI answer-path run. It never happens in `eval` (auto-approve), which is where `trajectory_ok` is computed. Do not widen the formula.
- **The ad-hoc script and A3/A4 of live-validation** grep the `sync (code)` line, printed before the pause. Keep that line's text byte-identical.
- **Fault switch on resume:** `_fault()` runs at build time in each process; `finalize_once` keys on `account_attempts == 0`, so it cannot fire on a follow-up; `policy_date_mismatch` only touches the first `policy_node`.
- **LangSmith:** three root runs per complaint are expected; `trace --last N` counts roots, and approve-only resumes have zero prompt tokens and are skipped. For A4, `--last 2` sees the answer-path resume and the first run.
- **`InMemorySaver` in `eval` holds 12 threads of state in memory** until the process ends. Fine at this size; do not add pruning.

## 12. Open questions

All closed. A scratchpad spike on the installed langgraph 1.2.17 (2026-10-26) confirmed the API in §6: a paused `invoke` returns the state plus `__interrupt__` with the payload in `.value`; `get_state(config).next` is `('review',)` while paused and `()` after; `get_state(...).tasks[0].interrupts[0].value` holds the pending payload; an unknown thread returns empty `values`; `invoke(Command(resume={...}), config)` continues through the join-fed node. `pip index` resolves `langgraph-checkpoint-sqlite` to 3.1.2, so the `>=3,<4` pin stands.

## Execution-discipline note

Read the whole plan first. Execute the steps in order; each ends green. Before each step re-read §3 Budgets and §4 File inventory; on hitting a budget (a file over its fence, a fourth state field, a second interrupt, a prompt change, a file outside the inventory) stop and ask with the reason, instead of exceeding it quietly. If the spike in step 1 contradicts the API in §6, fix §6 first and say so. If A6 regresses, stop; the pause must not change a decision. On a single hard blocker leave a `TODO(<area>): <blocker>` and a compiling stand-in, keep going, report it. Finish with the fresh-context review in step 10.
