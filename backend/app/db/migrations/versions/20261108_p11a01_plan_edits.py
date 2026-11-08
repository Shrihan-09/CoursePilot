"""Prompt 11 Part A: course options, swaps, locks and undo.

Revision ID: p11a01; Revises: cf01.

* `student_plan.revision` (int, default 1) - a preview applies only to the
  revision it was computed from; `student_plan.edits` (JSON, default {}) - the
  student's own locks / selections / avoids / lightened terms.
* `student_plan_history` - the last few versions of a saved plan (undo).
* `plan_change_preview` - proposed changes; never the plan.
* `student_course.doing_well` (nullable bool) - the student's own "doing well"
  mark on an in-progress course (suggestion ordering only; not an audit input).
* `student_planning_profile.use_grades_for_suggestions` (bool, default true).

Existing saved plans get revision 1 and no edits: unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "p11a01"
down_revision = "cf01"
branch_labels = None
depends_on = None


def _plan_columns():
    return [
        sa.Column("program_version_id", sa.Uuid(),
                  sa.ForeignKey("program_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("catalog_year", sa.String(16), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("planning_engine_version", sa.String(32), nullable=False),
        sa.Column("audit_engine_version", sa.String(64), nullable=False),
        sa.Column("academic_fingerprint", sa.String(64), nullable=False),
        sa.Column("rules_fingerprint", sa.String(64), nullable=False),
        sa.Column("offering_dataset", sa.JSON(), nullable=False),
        sa.Column("plan_status", sa.String(32), nullable=False),
        sa.Column("result_blob", sa.LargeBinary(), nullable=False),
    ]


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]


def upgrade():
    op.add_column("student_plan", sa.Column("revision", sa.Integer(), nullable=False,
                                            server_default="1"))
    op.add_column("student_plan", sa.Column("edits", sa.JSON(), nullable=False,
                                            server_default="{}"))
    op.create_table(
        "student_plan_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("start_term", sa.String(5), nullable=False),
        sa.Column("edits", sa.JSON(), nullable=False, server_default="{}"),
        *_plan_columns(), *_timestamps())
    op.create_index("ix_student_plan_history_student_id", "student_plan_history",
                    ["student_id"])
    op.create_table(
        "plan_change_preview",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("change", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("edits", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_plan_columns(), *_timestamps())
    op.create_index("ix_plan_change_preview_student_id", "plan_change_preview", ["student_id"])
    op.add_column("student_course", sa.Column("doing_well", sa.Boolean(), nullable=True))
    op.add_column("student_planning_profile",
                  sa.Column("use_grades_for_suggestions", sa.Boolean(), nullable=False,
                            server_default=sa.true()))


def downgrade():
    op.drop_column("student_planning_profile", "use_grades_for_suggestions")
    op.drop_column("student_course", "doing_well")
    op.drop_index("ix_plan_change_preview_student_id", table_name="plan_change_preview")
    op.drop_table("plan_change_preview")
    op.drop_index("ix_student_plan_history_student_id", table_name="student_plan_history")
    op.drop_table("student_plan_history")
    op.drop_column("student_plan", "edits")
    op.drop_column("student_plan", "revision")
