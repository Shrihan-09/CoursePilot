"""The assistant over the database (Prompt 11 Part B): caps, history, tokens.

    handle_message   daily cap -> history + flow -> Assistant.turn -> tokens for
                     the reply's cards -> persist -> usage counts
    execute_action   a token from a tap -> actions.execute -> persist
    delete_history   every conversation, message and unused token of the student

## Tokens

A card's action becomes a random 32-byte token. The response carries the
token; the database keeps only its SHA-256, the student, the action and an
expiry. A token is single-use, expires in 30 minutes, and is honoured only
for the student it was minted for. History re-renders cards WITHOUT tokens
(an old card cannot be tapped; previews expire anyway).

## Logging

Counts only: intent, tool calls, model calls, tokens, escalation, latency. No
message text, no tool output, no grades or GPA ever reach a log line.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.llm.base import ProviderNotConfiguredError, Role
from app.models import Student
from app.models.assistant import (
    AssistantAction,
    AssistantConversation,
    AssistantMessage,
    AssistantUsage,
)
from app.services.assistant import actions
from app.services.assistant.agent import SUGGESTIONS, AgentConfig, Assistant
from app.services.assistant.contracts import AssistantReply

logger = logging.getLogger(__name__)
TOKEN_TTL = timedelta(minutes=30)
HISTORY_FOR_MODEL = 6
HISTORY_SHOWN = 60


class AssistantUnavailable(RuntimeError):
    """No provider is configured (ASSISTANT_PROVIDER=none, or no API key)."""


class DailyLimitReached(RuntimeError):
    """The student used today's message allowance."""


class InvalidAction(LookupError):
    """The token is unknown, used, expired, or another student's."""


def providers(settings):
    from app.llm.registry import build_assistant_provider

    try:
        default = build_assistant_provider(settings, Role.ASSISTANT)
    except ProviderNotConfiguredError as exc:
        raise AssistantUnavailable(str(exc)) from None
    escalation = None
    if settings.assistant_escalation_enabled:
        try:
            escalation = build_assistant_provider(settings, Role.ASSISTANT_ESCALATION)
        except ProviderNotConfiguredError:
            escalation = None
    return default, escalation


def availability(settings) -> tuple[bool, str | None]:
    try:
        providers(settings)
    except AssistantUnavailable:
        if settings.assistant_provider == "none":
            return False, "The assistant is turned off on this server."
        return False, "The assistant isn't set up on this server yet."
    return True, None


def _today() -> datetime:
    return datetime.now(UTC)


def _usage(session: Session, student: Student, *, create: bool = False) -> AssistantUsage | None:
    day = _today().date()
    row = session.scalar(select(AssistantUsage).where(AssistantUsage.student_id == student.id,
                                                      AssistantUsage.day == day))
    if row is None and create:
        row = AssistantUsage(student_id=student.id, day=day, messages=0, model_calls=0,
                             input_tokens=0, cached_input_tokens=0, output_tokens=0,
                             escalated_turns=0)
        session.add(row)
        session.flush()
    return row


def remaining_today(session: Session, student: Student, settings) -> int:
    row = _usage(session, student)
    return max(0, settings.assistant_daily_message_limit - (row.messages if row else 0))


class ConversationNotFound(LookupError):
    pass


def conversation_title(text: str) -> str:
    return " ".join(re.findall(r"\S+", text.strip())[:6])[:160].rstrip() or "New chat"


def conversation_view(conv: AssistantConversation) -> dict:
    return {"id": str(conv.id), "title": conv.title, "archived": conv.archived,
            "created_at": conv.created_at.isoformat(), "updated_at": conv.updated_at.isoformat()}


def create_conversation(session: Session, student: Student) -> AssistantConversation:
    conv = AssistantConversation(student_id=student.id, title="New chat", archived=False)
    session.add(conv)
    session.flush()
    return conv


def _conversation(session: Session, student: Student,
                  conversation_id: uuid.UUID | None = None) -> AssistantConversation:
    stmt = select(AssistantConversation).where(AssistantConversation.student_id == student.id)
    if conversation_id is not None:
        conv = session.scalar(stmt.where(AssistantConversation.id == conversation_id))
        if conv is None:
            raise ConversationNotFound()
        return conv
    conv = session.scalar(stmt.where(AssistantConversation.archived.is_(False))
                          .order_by(AssistantConversation.updated_at.desc()).limit(1))
    return conv if conv is not None else create_conversation(session, student)


def conversations(session: Session, student: Student) -> list[dict]:
    return [conversation_view(c) for c in session.scalars(select(AssistantConversation)
            .where(AssistantConversation.student_id == student.id)
            .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc()))]


def delete_conversation(session: Session, student: Student, conversation_id: uuid.UUID) -> None:
    conv = _conversation(session, student, conversation_id)
    session.delete(conv)
    session.flush()


def _messages(session: Session, student: Student, limit: int,
              conversation_id: uuid.UUID | None = None) -> list[AssistantMessage]:
    conv = _conversation(session, student, conversation_id)
    rows = session.scalars(select(AssistantMessage)
                           .where(AssistantMessage.student_id == student.id,
                                  AssistantMessage.conversation_id == conv.id)
                           .order_by(AssistantMessage.created_at.desc(),
                                     AssistantMessage.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def history(session: Session, student: Student, conversation_id: uuid.UUID | None = None) -> list[dict]:
    return [{"id": str(m.id), "role": m.role, "content": m.content,
             "reply": (m.payload or {}).get("reply"),
             "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in _messages(session, student, HISTORY_SHOWN, conversation_id)]


def delete_history(session: Session, student: Student) -> int:
    n = session.scalar(select(func.count()).select_from(AssistantMessage)
                       .where(AssistantMessage.student_id == student.id)) or 0
    session.execute(delete(AssistantAction).where(AssistantAction.student_id == student.id))
    session.execute(delete(AssistantMessage).where(AssistantMessage.student_id == student.id))
    session.execute(delete(AssistantConversation)
                    .where(AssistantConversation.student_id == student.id))
    session.flush()
    return n


def _mint(session: Session, student: Student, action: dict, conversation_id=None) -> str:
    token = secrets.token_urlsafe(32)
    session.add(AssistantAction(student_id=student.id, conversation_id=conversation_id,
                                token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                                kind=str(action.get("kind")), payload=action,
                                expires_at=_today() + TOKEN_TTL))
    return token


def _tokenize(session: Session, student: Student, reply: AssistantReply, conversation_id=None) -> None:
    for card in [*reply.cards, *reply.proposals]:
        if card.action:
            card.action_token = _mint(session, student, card.action, conversation_id)


def _stored(reply: AssistantReply) -> dict:
    """The reply as history keeps it: no tokens, plus the flow state."""
    view = reply.model_dump(mode="json")
    for key in ("cards", "proposals"):
        for c in view.get(key) or []:
            c["action_token"] = None
    return {"reply": view, "flow": reply.flow}


def _persist(session: Session, student: Student, user_text: str | None,
             reply: AssistantReply, conversation_id=None) -> None:
    conv = _conversation(session, student, conversation_id)
    now = _today()
    conv.updated_at = now
    if user_text and conv.title == "New chat" and not session.scalar(select(AssistantMessage.id).where(
            AssistantMessage.conversation_id == conv.id, AssistantMessage.role == "user").limit(1)):
        conv.title = conversation_title(user_text)
    if user_text:
        session.add(AssistantMessage(conversation_id=conv.id, student_id=student.id,
                                     role="user", content=user_text[:2_000], payload={},
                                     created_at=now))
    session.add(AssistantMessage(conversation_id=conv.id, student_id=student.id,
                                 role="assistant", content=reply.text, payload=_stored(reply),
                                 created_at=now + timedelta(microseconds=1)))


def answers_from_chip(chip: dict | None) -> tuple[str, dict | None]:
    """A tapped chip: (text to send, answers). Prefixed values are commands."""
    if not chip:
        return "", None
    value = str(chip.get("value") or "")
    question = chip.get("question")
    for prefix in ("sections", "webreg", "more_like"):
        if value.startswith(prefix + ":"):
            return "", {prefix: value[len(prefix) + 1:]}
    if question:
        return "", {question: value}
    return value, None


async def handle_message(session: Session, student: Student, settings, *, text: str,
                         chip: dict | None = None, answers: dict | None = None,
                         on_activity=None, tools=None, conversation_id=None, context_term=None, builder_selection=None) -> dict:
    from app.services.assistant.db_tools import DbAssistantTools

    started = time.perf_counter()
    if remaining_today(session, student, settings) <= 0:
        raise DailyLimitReached(
            f"You've used today's {settings.assistant_daily_message_limit} assistant messages. "
            "They reset at midnight UTC.")
    conv = _conversation(session, student, conversation_id)
    default, escalation = providers(settings)
    chip_text, chip_answers = answers_from_chip(chip)
    text = (text or chip_text or "").strip()[:settings.explanation_max_query_chars * 3]
    answers = {**(chip_answers or {}), **(answers or {})} or None
    past = _messages(session, student, HISTORY_FOR_MODEL + 2, conv.id)
    flow = next(((m.payload or {}).get("flow") for m in reversed(past)
                 if m.role == "assistant"), None)
    hist = [{"role": m.role, "content": m.content} for m in past][-HISTORY_FOR_MODEL:]
    assistant = Assistant(tools or DbAssistantTools(session, student), default=default,
                          escalation=escalation, config=AgentConfig.from_settings(settings))
    context = f" (For term {context_term})" if context_term else ""
    if builder_selection is not None:
        context += (" [Semester builder UNSAVED draft selection: " + ", ".join(builder_selection)
                    + ". Suggest additions only; do not replace or apply. The student adds to the draft and confirms a separate preview.]")
    result = await assistant.turn(text + context, history=hist, flow=flow, answers=answers,
                                  on_activity=on_activity)
    reply = result.reply
    _tokenize(session, student, reply, conv.id)
    shown = text or (chip or {}).get("label") or ""
    _persist(session, student, shown, reply, conv.id)
    usage = _usage(session, student, create=True)
    usage.messages += 1
    usage.model_calls += result.usage.calls
    usage.input_tokens += result.usage.input_tokens
    usage.cached_input_tokens += result.usage.cached_input_tokens
    usage.output_tokens += result.usage.output_tokens
    usage.escalated_turns += int(result.escalated)
    session.commit()
    logger.info("assistant_turn", extra={
        "intent": result.route.intent if result.route else None,
        "generated_by": reply.generated_by, "tool_calls": len(result.tool_calls),
        "model_calls": result.usage.calls, "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens, "escalated": result.escalated,
        "gate_rejections": len(result.grounding_failures),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1)})
    return {"reply": reply.model_dump(mode="json"),
            "remaining_today": remaining_today(session, student, settings), "conversation_id": str(conv.id)}


def execute_action(session: Session, student: Student, token: str, *, tools=None) -> dict:
    from app.services.assistant.db_tools import DbAssistantTools

    digest = hashlib.sha256((token or "").encode()).hexdigest()
    row = session.scalar(select(AssistantAction).where(AssistantAction.token_sha256 == digest)
                         .with_for_update())
    now = _today()
    expires = None
    if row is not None:
        expires = row.expires_at if row.expires_at.tzinfo else \
            row.expires_at.replace(tzinfo=UTC)
    if row is None or row.student_id != student.id or row.used_at is not None or expires < now:
        raise InvalidAction("This option has expired. Ask again for fresh options.")
    # Single use, even if the action fails: committed BEFORE it runs, so no
    # rollback inside the action can make the token valid again. The row lock
    # (FOR UPDATE above) means a double tap sees used_at and is refused.
    row.used_at = now
    session.commit()
    reply = actions.execute(tools or DbAssistantTools(session, student), row.payload)
    _tokenize(session, student, reply, row.conversation_id)
    _persist(session, student, None, reply, row.conversation_id)
    session.commit()
    logger.info("assistant_action", extra={"kind": row.kind,
                                           "generated_by": reply.generated_by,
                                           "undo": reply.undo_available})
    return {"reply": reply.model_dump(mode="json")}


__all__ = ["SUGGESTIONS", "AssistantUnavailable", "DailyLimitReached", "InvalidAction",
           "answers_from_chip", "availability", "delete_history", "execute_action",
           "handle_message", "history", "providers", "remaining_today"]
