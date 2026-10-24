"""Optional expected graduation term on the planning profile.

Revision ID: 73gradterm01; Revises: 73workload01.
Additive and nullable: no existing profile states one, so the downgrade drops
the column without losing a derived fact.
"""

import sqlalchemy as sa
from alembic import op

revision = "73gradterm01"
down_revision = "73workload01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("student_planning_profile",
                  sa.Column("expected_graduation_term", sa.String(5), nullable=True))


def downgrade():
    op.drop_column("student_planning_profile", "expected_graduation_term")
