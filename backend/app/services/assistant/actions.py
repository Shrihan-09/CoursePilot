"""What a tap does (Prompt 11 Part B, B1/B1b). The ONLY path to an apply.

    choose_option     "Choose this" on an option card. The tap is the student's
                      confirmation for that one swap: preview -> apply, unless
                      the preview has a big side effect (moves another course,
                      delays graduation, exceeds the credit max, loses a
                      requirement) - then the diff comes back for one more tap.
    confirm_change    "Confirm" on a proposal card: applies that preview. If the
                      plan changed since, the change is previewed again and
                      shown again - a stale diff is never applied.
    confirm_preference
                      "Confirm" on a preference card.

`service.execute_action` reaches this only with a token minted for this
student when the card was shown, spent once. A model has no way to call it.
"""

from __future__ import annotations

from app.domain.plan_edits import PlanChange
from app.services.assistant.cards import change_title, proposal_card
from app.services.assistant.contracts import AssistantReply, Chip, term_label
from app.services.assistant.tools import AssistantTools


class StaleProposal(RuntimeError):
    """The plan changed since this preview was computed."""


def _errors(preview: dict) -> str:
    return "; ".join(e.split(":", 1)[-1].strip() for e in preview.get("errors") or [])


def _applied(preview: dict, applied: dict) -> AssistantReply:
    change = preview.get("change") or {}
    summary = (preview.get("diff") or {}).get("summary") or "Your plan was updated."
    term = change.get("to_term") or change.get("term_code")
    course = change.get("replacement") or change.get("course")
    chips = []
    if term and change.get("kind") == "remove":
        chips.append(Chip(label="Suggest a replacement", value=f"suggest_replacement:{term}"))
    if course and term and change.get("kind") in ("swap", "add", "move"):
        chips.append(Chip(label="Pick a section that fits my week",
                          value=f"sections:{course}|{term}"))
    if term and change.get("kind") != "remove":
        chips.append(Chip(label="Copy index numbers for WebReg", value=f"webreg:{term}"))
    when = term_label(term) if term else None
    follow = (f" Want me to pick a section of {course} that fits your week in {when}?"
              if chips and course and when and change.get("kind") in ("swap", "add", "move")
              else "")
    return AssistantReply(text=f"Done. {summary}.{follow}".replace("..", "."),
                          intent="change_request", generated_by="action",
                          applied_summary=summary, undo_available=True, suggestions=chips)


def execute(tools: AssistantTools, action: dict) -> AssistantReply:
    kind = action.get("kind")
    if kind == "choose_option":
        change = PlanChange.model_validate(action["change"])
        preview = tools.preview_plan_change(change)
        if not preview.get("valid"):
            return AssistantReply(text=f"That option can't be applied now: {_errors(preview)}.",
                                  intent="change_request", generated_by="action")
        if (preview.get("diff") or {}).get("big"):
            names = {}
            card = proposal_card(preview, title=change_title(preview.get("change") or {},
                                                              names), reason="second_tap")
            return AssistantReply(
                text=("This choice has a bigger effect on your plan - review it and tap "
                      "Confirm to make it."), intent="change_request", generated_by="action",
                proposals=[card])
        return _applied(preview, tools.apply_confirmed(preview["preview_id"]))
    if kind == "confirm_change":
        try:
            applied = tools.apply_confirmed(action["preview_id"])
        except StaleProposal:
            preview = tools.preview_plan_change(PlanChange.model_validate(action["change"]))
            if not preview.get("valid"):
                return AssistantReply(
                    text=f"Your plan changed, and this change can't be made now: "
                         f"{_errors(preview)}.", intent="change_request",
                    generated_by="action")
            card = proposal_card(preview, title=change_title(preview.get("change") or {}, {}),
                                 reason="refreshed")
            return AssistantReply(
                text="Your plan changed since this preview, so here is its effect now. Tap "
                     "Confirm again to make it.", intent="change_request",
                generated_by="action", proposals=[card])
        preview = {"change": action.get("change") or {}, "diff": applied.get("diff") or {}}
        return _applied(preview, applied)
    if kind == "confirm_preference":
        out = tools.apply_preference(action["preference"])
        return AssistantReply(text=f"Saved: {out.get('summary', 'your preferences')}. Rebuild "
                              "your plan on the Plan page to use them.",
                              intent="preference_change", generated_by="action")
    raise ValueError(f"unknown action {kind!r}")


__all__ = ["StaleProposal", "execute"]
