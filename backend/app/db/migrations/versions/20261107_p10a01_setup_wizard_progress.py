"""Prompt 10a: first-run setup wizard - saved progress and the stated first semester.

Revision ID: p10a01; Revises: t2quest02.

* `student.setup_steps` - the wizard steps the student has finished or skipped
  (JSON list of step keys), so a student who leaves resumes at the first
  unfinished step. NULL: none yet. Navigation state only; no engine reads it.
* `student.first_term` - the first Rutgers semester AS THE STUDENT STATES IT
  (e.g. "20259"). Shown back to them; planning still derives the entry term
  from the record (no new planning logic). NULL: not stated. The exact shape
  (four digits + 0/1/7/9) is validated by the service; the CHECK is portable
  (SQLite builds this schema in the ingestion tests).

Additive and nullable, so the downgrade drops the columns without losing a
derived fact.

NOTE: task 3 (t3dn01) also revises t2quest02. Whichever merges second points
its down_revision at the other so there is one head.
"""

import sqlalchemy as sa
from alembic import op

revision = "p10a01"
down_revision = "t2quest02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("student", sa.Column("setup_steps", sa.JSON(), nullable=True))
    op.add_column("student", sa.Column("first_term", sa.String(5), nullable=True))
    op.create_check_constraint(
        "student_first_term_shape", "student",
        "first_term IS NULL OR length(first_term) = 5")


def downgrade():
    op.drop_constraint("student_first_term_shape", "student", type_="check")
    op.drop_column("student", "first_term")
    op.drop_column("student", "setup_steps")
