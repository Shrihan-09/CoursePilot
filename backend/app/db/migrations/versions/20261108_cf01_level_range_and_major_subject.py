"""Correctness fixes: one credit range per profile; major-subject-each-term preference.

Revision ID: cf01; Revises: t3dn01.

* Item 6 (data): a confirmed Planning Profile with a workload level plans
  against ONE credit range - the student's own (credit_min/credit_max) when
  set, else the level's (Light 12-14, Balanced 14-17, Semi-rigorous 16-19,
  Challenging 17-21; app.services.planning.workload.LEVELS). The older
  target/maximum fields kept their pre-level values (15 / 18) and were shown in
  the plan header beside the level's 16-19. They are rewritten to that range.
  Profiles without a level are untouched (their target/maximum ARE the range).
  The `version` is not bumped: this is the same confirmed choice, stated once.
* Item 5 (schema): `student_planning_profile.major_subject_each_term` -
  "at least one course in my major's own subject each term (when eligible)",
  NOT NULL, default true (existing profiles get the default).

Downgrade drops the column. The target/maximum rewrite is not reversed: the
old values contradicted the level the student chose, and the plan never used
them once a level was set.
"""

import sqlalchemy as sa
from alembic import op

revision = "cf01"
down_revision = "t3dn01"
branch_labels = None
depends_on = None

#: Frozen copy of LEVELS (floor, maximum) at this revision.
LEVEL_RANGES = {"light": (12, 14), "balanced": (14, 17), "semi_rigorous": (16, 19),
                "challenging": (17, 21)}


def upgrade():
    op.add_column("student_planning_profile",
                  sa.Column("major_subject_each_term", sa.Boolean(), nullable=False,
                            server_default=sa.true()))
    conn = op.get_bind()
    # The student's own range wins over the level's.
    conn.execute(sa.text(
        "UPDATE student_planning_profile SET target_credits_per_term = credit_min, "
        "max_credits_per_term = credit_max WHERE workload_level IS NOT NULL "
        "AND credit_min IS NOT NULL AND credit_max IS NOT NULL"))
    for level, (floor, cap) in LEVEL_RANGES.items():
        conn.execute(sa.text(
            "UPDATE student_planning_profile SET target_credits_per_term = :floor, "
            "max_credits_per_term = :cap WHERE workload_level = :level "
            "AND (credit_min IS NULL OR credit_max IS NULL)"),
            {"floor": floor, "cap": cap, "level": level})


def downgrade():
    op.drop_column("student_planning_profile", "major_subject_each_term")
