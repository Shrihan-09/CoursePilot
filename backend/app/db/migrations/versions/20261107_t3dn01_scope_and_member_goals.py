"""Task 3: what-if Degree Navigator imports; member goals of one CoursePilot node.

Revision ID: t3dn01; Revises: p10a02.

* `degree_audit_import.confirmed_scope` - primary | what_if. NULL on every
  existing row means primary (the only kind that existed); a what-if import is
  a DN block for ANOTHER program, confirmed only for that program's what-if
  plans - never the student's record or main plan.
* `degree_audit_requirement_row.mapping_role` - node | member_goal. A member
  goal (AHo/AHp/AHq/AHr) belongs to one CoursePilot node (CORE_AH) together
  with its siblings; it never decides that node's status. NULL means node.

Additive and nullable, so the downgrade drops the columns without losing a
derived fact.
"""

import sqlalchemy as sa
from alembic import op

revision = "t3dn01"
down_revision = "p10a02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("degree_audit_import",
                  sa.Column("confirmed_scope", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "confirmed_scope_known", "degree_audit_import",
        "confirmed_scope IS NULL OR confirmed_scope IN ('primary','what_if')")
    op.add_column("degree_audit_requirement_row",
                  sa.Column("mapping_role", sa.String(length=16), nullable=True))


def downgrade():
    op.drop_column("degree_audit_requirement_row", "mapping_role")
    op.drop_constraint("confirmed_scope_known", "degree_audit_import", type_="check")
    op.drop_column("degree_audit_import", "confirmed_scope")
