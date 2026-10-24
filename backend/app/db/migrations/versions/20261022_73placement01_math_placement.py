"""Optional math placement on the student profile.

Revision ID: 73placement01; Revises: 73termwatch01.
Additive and nullable: no existing student states a placement, so the
downgrade drops the column without losing a derived fact.
"""

import sqlalchemy as sa
from alembic import op

revision = "73placement01"
down_revision = "73termwatch01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("student", sa.Column("math_placement", sa.String(16), nullable=True))


def downgrade():
    op.drop_column("student", "math_placement")
