"""Prompt 09 questionnaire: the student's own credit and major-related ranges.

Revision ID: t2quest02; Revises: t2quest01.
Additive and nullable: NULL means "the workload level's range" - what a
skipped question means - so the downgrade drops the columns without losing a
derived fact.
"""

import sqlalchemy as sa
from alembic import op

revision = "t2quest02"
down_revision = "t2quest01"
branch_labels = None
depends_on = None

COLUMNS = ("credit_min", "credit_max", "major_courses_min", "major_courses_max")


def upgrade():
    for name in COLUMNS:
        op.add_column("student_planning_profile", sa.Column(name, sa.Integer(), nullable=True))


def downgrade():
    for name in reversed(COLUMNS):
        op.drop_column("student_planning_profile", name)
