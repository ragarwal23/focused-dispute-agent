"""review: the human pause after synthesize. No LLM; the rep decides and code records.

The graph stops at the `review` node (a LangGraph interrupt) and hands the caller a `ReviewRequest`: the
recommended route, any open duplicate case, the draft and its open questions. The rep answers with a `ReviewDecision`:

    approve   file the case under the recommended route
    override  file it under another route, with a reason
    answer    only when the route is need_more_evidence and no follow-up is used yet: the answer is added to the
              complaint and the Account Agent looks again once (docs/plans/human-review.md, H1-H3)

The eval never pauses for a person: `orchestrator.run.investigate` answers every pause with `APPROVE`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator

from dispute_agent.data import Route

Action = Literal["approve", "override", "answer"]


class ReviewRequest(BaseModel):
    """What the rep sees at the pause. JSON-serializable: it is the interrupt payload."""

    thread_label: str
    route: Route  # the recommended route: the draft's, or manual_escalation when there is no draft
    existing_case_id: str | None  # an open case for the same dispute; approving files nothing new
    draft: dict[str, Any] | None  # None when synthesize failed
    open_questions: list[str]
    can_answer: bool  # route is need_more_evidence and the one follow-up is unused
    account_attempts: int
    policy_attempts: int
    followups: int


class ReviewDecision(BaseModel):
    """The rep's decision at the pause."""

    action: Action
    route: Route | None = None  # override only
    reason: str | None = None  # override only, required
    answer: str | None = None  # answer only, required

    @model_validator(mode="after")
    def _shape(self) -> ReviewDecision:
        if self.action == "override" and not (self.route and self.reason):
            raise ValueError("override needs a route and a reason")
        if self.action == "answer" and not self.answer:
            raise ValueError("answer needs the rep's text")
        if self.action != "override" and (self.route or self.reason):
            raise ValueError("route and reason go with override only")
        if self.action != "answer" and self.answer:
            raise ValueError("answer text goes with answer only")
        return self


class Pause(BaseModel):
    """A run stopped at `review`, waiting for `resume`."""

    thread_id: str
    request: ReviewRequest


APPROVE = ReviewDecision(action="approve")


def build_request(s: dict[str, Any], label: str, max_followups: int, existing_case_id: str | None) -> ReviewRequest:
    """Render the pause payload from graph state. Reads only: the review node re-runs this on every resume."""
    route, draft = s["synthesis"].route, s["synthesis"].draft
    return ReviewRequest(
        thread_label=label,
        route=route,
        existing_case_id=existing_case_id,
        draft=draft.model_dump(mode="json") if draft else None,
        open_questions=draft.open_questions if draft else [],
        can_answer=route == "need_more_evidence" and s["followups"] < max_followups,
        account_attempts=s["account_attempts"],
        policy_attempts=s["policy_attempts"],
        followups=s["followups"],
    )


def with_answer(rep_message: str, answer: str) -> str:
    """Fold the rep's answer into the complaint, labelled, so every later node reads it without a prompt change."""
    return f"{rep_message}\n\nRep follow-up (after review): {answer}"
