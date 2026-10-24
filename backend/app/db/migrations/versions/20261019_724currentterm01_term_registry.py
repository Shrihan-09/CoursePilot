"""Rutgers term registry, planning strategy, first-party feedback foundation.

Revision ID: 724currentterm01; Revises: 722schedule01.
Additive. Downgrade REFUSES while student-provided data exists (course
feedback, verified identities, a non-default planning strategy); the term
registry is derived public metadata and is rebuilt by term discovery.
"""

import sqlalchemy as sa
from alembic import op

revision = "724currentterm01"
down_revision = "722schedule01"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ]


def upgrade():
    op.create_table(
        "rutgers_term",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("campus", sa.String(8), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("publication_state", sa.String(16), nullable=False),
        sa.Column("first_published_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("course_count", sa.Integer(), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("ingestion_state", sa.String(16), nullable=False),
        sa.Column("ingested_hash", sa.String(64)),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
        sa.Column("availability_supported", sa.Boolean(), nullable=False),
        sa.Column("disagreements", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("term_code", "campus", name="uq_rutgers_term_campus"),
    )
    op.create_index("ix_rutgers_term_term_code", "rutgers_term", ["term_code"])
    op.add_column("student_planning_profile",
                  sa.Column("strategy", sa.String(32), nullable=False, server_default="balanced"))
    op.create_table(
        "course_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("student.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("course_string", sa.String(32), nullable=False),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("instructor_key", sa.String(128), nullable=False, server_default=""),
        sa.Column("modality", sa.String(32)),
        sa.Column("workload", sa.SmallInteger(), nullable=False),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("student_id", "course_string", "term_code", "instructor_key",
                            name="uq_course_feedback_once"),
        sa.CheckConstraint("workload BETWEEN 1 AND 5", name="ck_feedback_workload"),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_feedback_difficulty"),
    )
    op.create_index("ix_course_feedback_student_id", "course_feedback", ["student_id"])
    op.create_index("ix_course_feedback_course_string", "course_feedback", ["course_string"])
    op.create_table(
        "instructor_external_identity",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instructor_key", sa.String(128), nullable=False),
        sa.Column("subject_code", sa.String(8), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("verification", sa.String(32), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("source", "external_id", name="uq_instructor_external_profile"),
        sa.CheckConstraint("verification = 'verified_manual'", name="ck_identity_verified_only"),
    )
    op.create_index("ix_instructor_external_identity_instructor_key",
                    "instructor_external_identity", ["instructor_key"])


def downgrade():
    bind = op.get_bind()
    feedback = bind.execute(sa.text("SELECT COUNT(*) FROM course_feedback")).scalar()
    identities = bind.execute(sa.text("SELECT COUNT(*) FROM instructor_external_identity")).scalar()
    strategies = bind.execute(sa.text(
        "SELECT COUNT(*) FROM student_planning_profile WHERE strategy <> 'balanced'")).scalar()
    if feedback or identities or strategies:
        raise RuntimeError(
            "Export student feedback, verified identities and planning strategies "
            "before downgrading 724currentterm01")
    op.drop_table("instructor_external_identity")
    op.drop_table("course_feedback")
    op.drop_column("student_planning_profile", "strategy")
    op.drop_index("ix_rutgers_term_term_code", table_name="rutgers_term")
    op.drop_table("rutgers_term")
