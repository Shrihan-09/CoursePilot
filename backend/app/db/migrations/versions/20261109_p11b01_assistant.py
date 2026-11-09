"""Prompt 11 Part B: the assistant's chat history, usage counts, action tokens,
and the sections a student chose for planned courses.

Revision ID: p11b01; Revises: p11a01.

* `assistant_conversation`, `assistant_message` - per-student chat history,
  deletable as a whole.
* `assistant_usage` - per student per day: message, model-call and token
  COUNTS (daily cap, cost). No content.
* `assistant_action` - one-time tokens behind "Choose this" / "Confirm"; only
  the token's SHA-256 is stored.
* `student_planned_section` - a chosen section per planned course and term.

New tables only: nothing existing changes, no backfill.
"""

import sqlalchemy as sa
from alembic import op

revision = "p11b01"
down_revision = "p11a01"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]


def _student():
    return sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id", ondelete="CASCADE"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "assistant_conversation",
        sa.Column("id", sa.Uuid(), primary_key=True), _student(), *_timestamps())
    op.create_index("ix_assistant_conversation_student_id", "assistant_conversation",
                    ["student_id"])
    op.create_table(
        "assistant_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(),
                  sa.ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
                  nullable=False),
        _student(),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        *_timestamps())
    op.create_index("ix_assistant_message_conversation_id", "assistant_message",
                    ["conversation_id"])
    op.create_index("ix_assistant_message_student_id", "assistant_message", ["student_id"])
    op.create_table(
        "assistant_usage",
        sa.Column("id", sa.Uuid(), primary_key=True), _student(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("escalated_turns", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("student_id", "day", name="uq_assistant_usage_day"))
    op.create_index("ix_assistant_usage_student_id", "assistant_usage", ["student_id"])
    op.create_table(
        "assistant_action",
        sa.Column("id", sa.Uuid(), primary_key=True), _student(),
        sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps())
    op.create_index("ix_assistant_action_student_id", "assistant_action", ["student_id"])
    op.create_table(
        "student_planned_section",
        sa.Column("id", sa.Uuid(), primary_key=True), _student(),
        sa.Column("term_code", sa.String(5), nullable=False),
        sa.Column("course_string", sa.String(32), nullable=False),
        sa.Column("index_number", sa.String(16), nullable=False),
        sa.Column("section_number", sa.String(16), nullable=True),
        sa.Column("section_id", sa.Uuid(),
                  sa.ForeignKey("course_section.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("chosen_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("student_id", "term_code", "course_string",
                            name="uq_student_planned_section_course"))
    op.create_index("ix_student_planned_section_student_id", "student_planned_section",
                    ["student_id"])
    op.create_index("ix_student_planned_section_term", "student_planned_section",
                    ["student_id", "term_code"])


def downgrade() -> None:
    op.drop_table("student_planned_section")
    op.drop_table("assistant_action")
    op.drop_table("assistant_usage")
    op.drop_table("assistant_message")
    op.drop_table("assistant_conversation")
