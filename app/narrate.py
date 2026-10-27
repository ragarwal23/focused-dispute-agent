"""narrate: the status key. Turns the graph's stream chunks into the plain-language steps the page shows. Pure, no I/O.

The server streams the graph with stream_mode=["debug", "updates"], subgraphs=True, so every chunk is
(namespace, mode, data). Shapes pinned on langgraph 1.2.17 (docs/history/web-ui.md, Phase A spike):

    debug    {"type": "task",        "payload": {"id", "name", "input", "triggers"}}             a node started
             {"type": "task_result", "payload": {"id", "name", "error", "result", "interrupts"}}  it finished
             {"type": "checkpoint", ...}                                                         ignored
    updates  {"__interrupt__": (Interrupt(value=<ReviewRequest json>),)}                        the review pause

Namespace () is the parent graph; ("account_agent:<uuid>",) is the Account Agent's own loop (llm_call, tool_node,
finalize), and the follow-up run arrives under its own node names. The tool_node task's input holds the
model's tool calls, so each tool gets its own line, with its real name, as soon as the node starts.
Every step is owned by "ai" (an LLM call), "code" (plain Python) or "you" (the rep), the brief's split made visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from dispute_agent.config import WORLD_TODAY

Owner = Literal["ai", "code", "you"]
Label = str | Callable[[dict[str, Any]], str]  # a fixed string, or built from the task's input (start) or result (done)

CATEGORY = {
    "late_fee_autopay": "late fee with autopay on",
    "missing_payment": "missing payment",
    "returned_reversed_payment": "returned or reversed payment",
    "refund_status": "refund status",
}


def _day(d: Any) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def _words(value: str) -> str:
    return value.replace("_", " ")


def _lookups(r: dict[str, Any]) -> str:
    return f"{r['account']['tool_calls_made']} lookups"


def _states(r: dict[str, Any]) -> str:
    d = r["derived"]
    return f"Payment state: {_words(d.payment_state)}; refund state: {_words(d.refund_state)}"


def _policy(r: dict[str, Any]) -> str:
    rules = r["policy"].rules
    return f"Policy read: {len(rules)} rules from {len({x.section_id for x in rules})} sections"


def _review(r: dict[str, Any]) -> str:
    d = r["review"]
    return {"approve": "You approved", "override": f"You overrode the route to {d.route}", "answer": "You answered"}[d.action]


def _case(r: dict[str, Any]) -> str:
    c = r["case"]
    return f"Case {c.case_id} filed under {c.route} ({'new' if c.created else 'already existed'})"


@dataclass(frozen=True)
class Step:
    owner: Owner
    start: Label
    done: Label  # "" hides the step once it is done


DERIVE = Step("code", "Working out the payment and refund status", _states)
STEPS: dict[str, Step] = {
    "classify": Step("ai", "Working out what kind of dispute this is", lambda r: f"Dispute type: {CATEGORY.get(r['category'], r['category'])}"),
    "account_agent": Step("ai", "Reading the account's records", lambda r: f"Account records read ({_lookups(r)})"),
    "llm_call": Step("ai", "Deciding what to look up next", ""),
    "tool_node": Step("code", "Looking things up", ""),
    "finalize": Step("ai", "Summarising what the records show", "Records summarised"),
    "tool:get_account": Step("code", "Pulling the account details", "Account details pulled"),
    "tool:list_events": Step("code", "Pulling the payment history", "Payment history pulled"),
    "tool:get_event": Step("code", lambda a: f"Looking at record {a.get('event_id', '?')}", lambda a: f"Record {a.get('event_id', '?')} read"),
    "tool:find_events_by_reference": Step(
        "code", lambda a: f'Searching for reference "{a.get("reference_fragment", "?")}" across accounts', "Reference search done"),
    "tool:list_review_cases": Step("code", "Checking for an existing review case", "Existing cases checked"),
    "derive_states": DERIVE,
    "policy_agent": Step("ai", f"Finding the policy in force on {_day(WORLD_TODAY)} and reading it", _policy),
    "sync": Step("code", "Cross-checking the account findings against the policy",
                 lambda r: "Cross-check: " + ("; ".join(r["sync"].issues) or "no issues")),
    "synthesize": Step("ai", "Drafting the review case", lambda r: (
        f"Draft written: route {r['synthesis'].draft.review_route}" if r["synthesis"].draft else "The assistant could not write a draft")),
    "review": Step("you", "Waiting for your decision", _review),
    "account_agent_followup": Step("ai", "Looking at the account again with your answer",
                                   lambda r: f"Account records read again ({_lookups(r)})"),
    "derive_states_followup": DERIVE,
    "create_review_case": Step("code", "Filing the review case", _case),
}


def _text(label: Label, data: dict[str, Any]) -> str:
    try:
        return label(data) if callable(label) else label
    except Exception:  # a payload shape we did not expect: say less, never break the stream
        return ""


def _json(content: Any) -> dict[str, Any]:
    try:
        out = json.loads(content)
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _detail(name: str, r: dict[str, Any]) -> str | None:
    """The one extra line under a finished step, where the step has something a rep should see."""
    if name == "synthesize" and not r["synthesis"].draft:
        return "; ".join(r["synthesis"].errors) or None
    return None


def _event(phase: str, sid: str, key: str, label: str, owner: Owner, parent: str | None, detail: str | None = None) -> dict[str, Any]:
    return {"type": "step", "phase": phase, "id": sid, "key": key, "label": label, "owner": owner, "parent": parent, "detail": detail}


def narrate(ns: tuple[str, ...], mode: str, data: Any) -> list[dict[str, Any]]:
    """One stream chunk -> zero or more UI events (`step` start/end, or the `pause`). Unknown shapes give nothing."""
    if mode == "updates":
        if isinstance(data, dict) and data.get("__interrupt__"):
            return [{"type": "pause", "request": data["__interrupt__"][0].value}]
        return []
    if mode != "debug" or not isinstance(data, dict) or data.get("type") not in ("task", "task_result"):
        return []
    payload = data.get("payload") or {}
    name, tid = str(payload.get("name", "?")), str(payload.get("id", ""))
    parent = ns[0].split(":")[0] if ns else None
    step = STEPS.get(name, Step("code", name, name))

    if data["type"] == "task":
        events = [_event("start", tid, name, _text(step.start, payload.get("input") or {}) or name, step.owner, parent)]
        if name == "tool_node":  # one line per tool call, named as soon as the model has asked for it
            messages = (payload.get("input") or {}).get("messages") or []
            for call in getattr(messages[-1], "tool_calls", None) or [] if messages else []:
                key = f"tool:{call['name']}"
                t = STEPS.get(key, Step("code", call["name"], call["name"]))
                events.append(_event("start", f"{tid}:{call['id']}", key, _text(t.start, call["args"]) or call["name"], t.owner, parent))
        return events

    if payload.get("interrupts"):  # review paused: the pause event, not a finished step, closes it
        return []
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if payload.get("error"):
        return [_event("end", tid, name, f"{_text(step.start, {}) or name}: failed", step.owner, parent, str(payload["error"]))]
    events = []
    if name == "tool_node":
        for m in result.get("messages", []):  # the done label reads the tool's JSON output (get_event returns the event)
            key = f"tool:{getattr(m, 'name', '?')}"
            t = STEPS.get(key, Step("code", key[5:], key[5:]))
            failed = getattr(m, "status", "success") == "error" or "budget exhausted" in str(m.content)
            label = f"{_text(t.start, {}) or key[5:]}: failed" if failed else _text(t.done, _json(m.content)) or key[5:]
            events.append(_event("end", f"{tid}:{m.tool_call_id}", key, label, t.owner, parent, str(m.content) if failed else None))
    try:
        detail = _detail(name, result)
    except Exception:
        detail = None
    events.append(_event("end", tid, name, _text(step.done, result) if step.done else "", step.owner, parent, detail))
    return events


def result_event(thread_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """The finished run as the page needs it: the case, who decided, the draft and its recommended route. No messages."""
    draft = values["synthesis"].draft
    return {
        "type": "result", "thread_id": thread_id,
        "case": values["case"].model_dump(mode="json"),
        "review": values["review"].model_dump(mode="json"),
        "draft": draft.model_dump(mode="json") if draft else None,
        "route": values["synthesis"].route,
    }
