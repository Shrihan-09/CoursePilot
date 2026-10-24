"""Workload levels, filler preference and pinned courses on the planning profile.

Revision ID: 73workload01; Revises: 73placement01.
Additive. Existing profiles get no workload level (they keep the Phase 7.2.1
pace behaviour until the student chooses one), no filler preference and no
pins; the downgrade drops the three columns.
"""

import sqlalchemy as sa
from alembic import op

revision = "73workload01"
down_revision = "73placement01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("student_planning_profile",
                  sa.Column("workload_level", sa.String(16), nullable=True))
    op.add_column("student_planning_profile",
                  sa.Column("filler_preference", sa.String(16), nullable=False,
                            server_default="no_preference"))
    # The '[]' default only fills existing rows (a JSON server default cannot
    # be compared by alembic); the application supplies the value afterwards.
    op.add_column("student_planning_profile",
                  sa.Column("pins", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.alter_column("student_planning_profile", "pins", server_default=None)


def downgrade():
    op.drop_column("student_planning_profile", "pins")
    op.drop_column("student_planning_profile", "filler_preference")
    op.drop_column("student_planning_profile", "workload_level")
