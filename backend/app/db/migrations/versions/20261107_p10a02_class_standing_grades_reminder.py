"""Prompt 10a follow-up: class standing, and the grades-are-in reminder.

Revision ID: p10a02; Revises: p10a01.

* `student.class_standing` - "What year are you?" AS THE STUDENT STATES IT:
  first_year | sophomore | junior | senior | transfer. It orders the setup
  wizard's upload cards only; no engine reads it. NULL: not stated.
* `student.grades_reminder_dismissed_term` - the term whose final-grades date
  the student dismissed the "Your grades are in" reminder for; it comes back
  for the next term's grades date. NULL: never dismissed.

Additive and nullable; the downgrade drops both.
"""

import sqlalchemy as sa
from alembic import op

revision = "p10a02"
down_revision = "p10a01"
branch_labels = None
depends_on = None

STANDINGS = "('first_year','sophomore','junior','senior','transfer')"


def upgrade():
    op.add_column("student", sa.Column("class_standing", sa.String(16), nullable=True))
    op.add_column("student", sa.Column("grades_reminder_dismissed_term", sa.String(5),
                                       nullable=True))
    op.create_check_constraint(
        "student_class_standing_known", "student",
        f"class_standing IS NULL OR class_standing IN {STANDINGS}")


def downgrade():
    op.drop_constraint("student_class_standing_known", "student", type_="check")
    op.drop_column("student", "grades_reminder_dismissed_term")
    op.drop_column("student", "class_standing")
