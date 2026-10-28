"""The web app's server: the rep's inbox, and the graph's steps streamed to the page as they happen.

    python -m app.server [--data data] [--port 8000] [--no-browser]      (or: make run)

One FastAPI app, made by `make_app(ds, sections, graph)` so tests can pass the fake graph. A run is the same graph
the CLI runs, with the same SQLite checkpoints (runs/checkpoints.sqlite), so a thread paused here can be resumed
with `python -m dispute_agent resume` and the other way round. The graph runs in a worker thread
(`graph.stream(..., stream_mode=["debug", "updates"], subgraphs=True)`); each chunk goes through `narrate` onto a
queue, and the response reads the queue as Server-Sent Events. Every stream ends with exactly one `pause`,
`result` or `error`. If the browser goes away, the worker keeps going and the run still pauses or finishes.
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import threading
import uuid
import webbrowser
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

from app.narrate import result_event, narrate
from dispute_agent.data import Dataset
from dispute_agent.orchestrator.graph import initial_graph_state
from dispute_agent.orchestrator.run import new_thread_id, thread_config
from dispute_agent.policy_agent.retrieve import PolicySection
from dispute_agent.review.review import ReviewDecision

TAGS = ["slice=investigate", "web"]
CHANNEL = {"clean": "Customer message", "messy": "Call notes"}
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
MAX_THREADS = 50  # the paused-runs list shows the newest this many
APP_DIST = Path(__file__).parent / "dist"  # the built page (`make build`), committed so running it needs no Node


class NewThread(BaseModel):
    account_id: str
    message: str
    label: str | None = None


def langsmith_url(run_id: str) -> str:
    """Asked only when the rep clicks "View trace": LangSmith ingests late, and the run path never waits on it."""
    from langsmith import Client

    return Client().read_run(run_id).url


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def free_port(start: int, tries: int = 20) -> int:
    """The first port from `start` that binds on 127.0.0.1, so another app on 8000 doesn't stop this one."""
    for port in range(start, start + tries):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"no free port in {start}-{start + tries - 1}; pass --port")


def make_app(
    ds: Dataset, sections: dict[str, PolicySection], graph: Any, trace_url: Callable[[str], str] = langsmith_url,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Dispute assistant")
    run_ids: dict[str, str] = {}  # thread_id -> the latest LangSmith run ID, for the trace link; lost on restart

    def snapshot(thread_id: str) -> Any:
        snap = graph.get_state(thread_config(thread_id))
        if not snap.values:
            raise HTTPException(404, f"no run for thread {thread_id}")
        return snap

    def pending(snap: Any) -> list[dict[str, Any]]:
        return [i.value for task in snap.tasks for i in task.interrupts]

    def sources(draft: dict[str, Any] | None, *more: str | None) -> dict[str, dict[str, Any]]:
        """What each cited ID is, for the chips' popovers. A memo is the record's own text: the page shows it as data."""
        ids = [*(draft or {}).get("event_ids", []), *(draft or {}).get("policy_source_ids", []), *more]
        out: dict[str, dict[str, Any]] = {}
        for i in ids:
            if (e := ds.events.get(i or "")) is not None:
                out[i] = {"kind": "event", "type": e.type, "date": e.occurred_at.date().isoformat(), "amount": e.amount,
                          "channel": e.channel, "memo": e.memo}
            elif (p := sections.get(i or "")) is not None:
                out[i] = {"kind": "policy", "title": p.heading, "doc": f"{p.doc_title} v{p.version}",
                          "effective_from": p.effective_from.isoformat(), "status": p.status}
        return out

    def with_sources(request: dict[str, Any]) -> dict[str, Any]:
        return {**request, "sources": sources(request.get("draft"))}

    def result(thread_id: str, values: dict[str, Any]) -> dict[str, Any]:
        out = result_event(thread_id, values)
        return {**out, "sources": sources(out["draft"], out["case"]["disputed_event_id"])}

    def summary(thread_id: str, snap: Any) -> dict[str, Any]:
        v, waiting = snap.values, pending(snap)
        status = "finished" if not snap.next else "paused" if waiting else "stopped"
        route = v["case"].route if v.get("case") else v["synthesis"].route if v.get("synthesis") else None
        return {
            "thread_id": thread_id, "label": v.get("thread_label", thread_id), "account_id": v.get("account_id"),
            "status": status, "route": route, "updated_at": snap.created_at, "stopped_at": list(snap.next) if status == "stopped" else [],
        }

    def stream(thread_id: str, step: Any, label: str, resuming: bool) -> StreamingResponse:
        run_id = str(uuid.uuid4())
        run_ids[thread_id] = run_id
        name = f"dispute {label} resume" if resuming else f"dispute {label}"
        config = {**thread_config(thread_id, [*TAGS, "resume"] if resuming else TAGS, name), "run_id": run_id}
        q: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def work() -> None:
            paused = False
            try:
                for ns, mode, data in graph.stream(step, config, stream_mode=["debug", "updates"], subgraphs=True):
                    for event in narrate(ns, mode, data):
                        if event["type"] == "pause":
                            paused = True
                            event.update(thread_id=thread_id, request=with_sources(event["request"]))
                        q.put(event)
                if not paused:
                    q.put(result(thread_id, graph.get_state(thread_config(thread_id)).values))
            except Exception as exc:  # API error, network: the checkpoint keeps every finished step
                q.put({"type": "error", "thread_id": thread_id, "message": f"{type(exc).__name__}: {exc}"})
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True).start()

        def body() -> Iterator[str]:
            while (event := q.get()) is not None:
                yield _sse(event)

        return StreamingResponse(body(), media_type="text/event-stream", headers={**SSE_HEADERS, "X-Thread-Id": thread_id})

    @app.get("/api/inbox")
    def inbox() -> dict[str, Any]:
        """Dev scenarios only, and never the answer key: the held-out split stays unrun, `expected_*` stays here."""
        items = [
            {"scenario_id": s.scenario_id, "account_id": s.account_id, "customer": ds.accounts[s.account_id].customer_name,
             "channel": CHANNEL[s.wording], "message": s.rep_message}
            for s in sorted(ds.scenarios.values(), key=lambda s: s.scenario_id) if s.split == "dev"
        ]
        return {"items": items}

    @app.get("/api/accounts")
    def accounts() -> dict[str, Any]:
        return {"items": [{"account_id": a.account_id, "customer": a.customer_name} for a in sorted(ds.accounts.values(), key=lambda a: a.account_id)]}

    @app.get("/api/threads")
    def threads() -> dict[str, Any]:
        """Every thread in the checkpointer, newest first (checkpoint IDs are time-ordered)."""
        seen: dict[str, None] = {}
        for t in graph.checkpointer.list(None):
            conf = t.config["configurable"]
            if not conf.get("checkpoint_ns"):
                seen.setdefault(conf["thread_id"], None)
            if len(seen) >= MAX_THREADS:
                break
        items = [summary(tid, graph.get_state(thread_config(tid))) for tid in seen]
        return {"items": sorted(items, key=lambda i: i["updated_at"] or "", reverse=True)}

    @app.get("/api/threads/{thread_id}")
    def thread(thread_id: str) -> dict[str, Any]:
        snap = snapshot(thread_id)
        out = summary(thread_id, snap)
        v, waiting = snap.values, pending(snap)
        out["message"] = v.get("rep_message")
        out["request"] = with_sources(waiting[0]) if waiting else None
        out["result"] = result(thread_id, v) if out["status"] == "finished" else None
        return out

    @app.post("/api/threads")
    def start(body: NewThread) -> StreamingResponse:
        if body.account_id not in ds.accounts:
            raise HTTPException(404, f"unknown account {body.account_id}; known: {', '.join(sorted(ds.accounts))}")
        if not body.message.strip():
            raise HTTPException(422, "the complaint is empty")
        label = body.label or body.account_id
        thread_id = new_thread_id(label)
        return stream(thread_id, initial_graph_state(body.account_id, body.message.strip(), label), label, resuming=False)

    @app.post("/api/threads/{thread_id}/resume")
    def resume(thread_id: str, decision: ReviewDecision) -> StreamingResponse:
        """The same checks as the CLI's `resume`, before anything reaches the graph (a refused answer would re-pause silently)."""
        snap = snapshot(thread_id)
        if not snap.next:
            raise HTTPException(409, f"thread {thread_id} already finished: case {snap.values['case'].case_id}")
        waiting = pending(snap)
        if decision.action == "answer" and waiting and not waiting[0]["can_answer"]:
            raise HTTPException(422, "this pause takes approve or override only")
        return stream(thread_id, Command(resume=decision.model_dump()), snap.values["thread_label"], resuming=True)

    @app.get("/api/threads/{thread_id}/trace")
    def trace(thread_id: str) -> dict[str, str]:
        run_id = run_ids.get(thread_id)
        if run_id is None:
            raise HTTPException(404, "no trace recorded for this thread in this server session")
        try:
            return {"url": trace_url(run_id)}
        except Exception as exc:
            raise HTTPException(404, f"trace not available yet: {type(exc).__name__}") from exc

    if static_dir is not None and (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="page")  # last, so /api/* wins
    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from dispute_agent.__main__ import CHECKPOINTS
    from dispute_agent.account_agent.agent import build_account_agent
    from dispute_agent.data import load_dataset
    from dispute_agent.orchestrator.graph import build_graph, sqlite_checkpointer
    from dispute_agent.policy_agent.retrieve import load_policies

    p = argparse.ArgumentParser(prog="python -m app.server", description="The rep's inbox and assistant (localhost only)")
    p.add_argument("--data", default="data", help="folder with the four .xlsx files")
    p.add_argument("--port", type=int, default=8000, help="first port to try; the next free one is used if it's taken")
    p.add_argument("--no-browser", action="store_true", help="don't open the page in a browser")
    args = p.parse_args(argv)
    ds = load_dataset(args.data)
    sections = load_policies(Path(args.data) / "policies")
    graph = build_graph(ds, sections, build_account_agent(ds), checkpointer=sqlite_checkpointer(CHECKPOINTS))
    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    if port != args.port:
        print(f"Port {args.port} is taken, using {port}.")
    if not (APP_DIST / "index.html").exists():
        print("The page isn't built (app/dist): run `make build`, or `cd app && npm run dev`. Serving the API only.")
    print(f"Dispute desk: {url}")
    if not args.no_browser:
        threading.Timer(1.0, _open, (url,)).start()
    uvicorn.run(make_app(ds, sections, graph, static_dir=APP_DIST), host="127.0.0.1", port=port)


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass  # headless: the printed URL is enough


if __name__ == "__main__":
    main()
