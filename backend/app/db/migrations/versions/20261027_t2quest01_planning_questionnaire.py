"""Task 2 planning questionnaire: commitments, honors answer, per-semester answers.

Revision ID: t2quest01; Revises: 76recs01.
Additive. Existing profiles are backfilled with "none" / no overrides (what a
skipped question means) through a temporary server default, which is then
dropped: the application always writes both columns. `honors` is nullable
(not answered). The downgrade drops the three columns; nothing else reads them.
"""

import sqlalchemy as sa
from alembic import op

revision = "t2quest01"
down_revision = "76recs01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("student_planning_profile",
                  sa.Column("commitments", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("student_planning_profile", sa.Column("honors", sa.Boolean(), nullable=True))
    op.add_column("student_planning_profile",
                  sa.Column("term_overrides", sa.JSON(), nullable=False, server_default="[]"))
    op.alter_column("student_planning_profile", "commitments", server_default=None)
    op.alter_column("student_planning_profile", "term_overrides", server_default=None)


def downgrade():
    op.drop_column("student_planning_profile", "term_overrides")
    op.drop_column("student_planning_profile", "honors")
    op.drop_column("student_planning_profile", "commitments")
