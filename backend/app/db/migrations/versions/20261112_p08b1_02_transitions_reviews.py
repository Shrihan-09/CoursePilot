"""Course transitions, review flags, home school, component reviews (Prompt 08 B1 follow-up).

Revision ID: p08b102; Revises: p08b101.

Additive:
  * course_transition - department-announced course changes (retired /
    announced courses, prerequisite equivalents), each with its source;
  * requirement.review_flags - a node counted under an assumption a human
    must confirm ("111 counts toward 121: stated for transfer credit");
  * program_rule type 'no_major_credit' - "SAS credit, but not Computer
    Science major credit";
  * student.home_school_code - the school a student is IN (a pre-business
    student is in SAS while planning an RBS major);
  * component_review - append-only human reviews of school components.
No existing row changes meaning; nothing is backfilled.
"""
import sqlalchemy as sa
from alembic import op

revision = "p08b102"
down_revision = "p08b101"
branch_labels = None
depends_on = None


def _ts():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                      nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                      nullable=False)]


def upgrade():
    op.create_table(
        "course_transition",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("course_string", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("seasons", sa.JSON()),
        sa.Column("title", sa.Text()),
        sa.Column("credits", sa.Numeric(4, 1)),
        sa.Column("satisfies_prerequisite_of", sa.JSON()),
        sa.Column("assumed_prerequisite", sa.JSON()),
        sa.Column("planner_note", sa.Text()),
        sa.Column("note_programs", sa.JSON()),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("needs_review", sa.JSON()),
        *_ts(),
        sa.UniqueConstraint("course_string", "kind", name="uq_course_transition"),
        sa.CheckConstraint("kind IN ('retired','announced')",
                           name=op.f("ck_course_transition_course_transition_kind_known")),
    )
    op.add_column("requirement", sa.Column("review_flags", sa.JSON()))
    op.add_column("student", sa.Column("home_school_code", sa.String(16)))
    op.drop_constraint(op.f("ck_program_rule_program_rule_type_known"), "program_rule",
                       type_="check")
    op.create_check_constraint(
        op.f("ck_program_rule_program_rule_type_known"), "program_rule",
        "rule_type IN ('max_grade_count','course_exclusion','residency','min_gpa',"
        "'residency_window','pass_fail_limit','double_count_limit','no_major_credit')")
    op.create_table(
        "component_review",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("component_id", sa.Uuid(),
                  sa.ForeignKey("school_requirement_set_version.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("definition_sha256", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text()),
        *_ts(),
        sa.CheckConstraint("decision IN ('approved','changes_requested')",
                           name=op.f("ck_component_review_component_review_decision_known")),
    )
    op.create_index("ix_component_review_component_id", "component_review", ["component_id"])


def downgrade():
    op.drop_table("component_review")
    op.drop_constraint(op.f("ck_program_rule_program_rule_type_known"), "program_rule",
                       type_="check")
    op.execute("DELETE FROM program_rule WHERE rule_type = 'no_major_credit'")
    op.create_check_constraint(
        op.f("ck_program_rule_program_rule_type_known"), "program_rule",
        "rule_type IN ('max_grade_count','course_exclusion','residency','min_gpa',"
        "'residency_window','pass_fail_limit','double_count_limit')")
    op.drop_column("student", "home_school_code")
    op.drop_column("requirement", "review_flags")
    op.drop_table("course_transition")
