"""Term-scoped SOC Core codes and the student's declaration status (Phase 7.6).

Revision ID: 76recs01; Revises: 74dnimport01.
Additive only:

* `offering_core_code` - SOC `coreCodes` per course offering (one row per
  offering and code), loaded by the course pipeline and backfilled from the
  archived payloads (scripts/backfill_core_codes.py). Derived public data:
  the downgrade drops it, and a re-run of the backfill restores it.
* `student.declaration_status` - NULL (not stated, read as declared),
  'declared' or 'undeclared'. Nullable with no default, so every existing
  record keeps its meaning. The downgrade REFUSES while any student has
  stated a status: that is the student's own statement, not dropped silently.
"""

import sqlalchemy as sa
from alembic import op

revision = "76recs01"
down_revision = "74dnimport01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("student", sa.Column("declaration_status", sa.String(16), nullable=True))
    op.create_check_constraint(
        op.f("ck_student_student_declaration_status_known"), "student",
        "declaration_status IS NULL OR declaration_status IN ('declared','undeclared')")
    op.create_table(
        "offering_core_code",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("offering_id", sa.Uuid(),
                  sa.ForeignKey("course_offering.id", ondelete="CASCADE",
                                name=op.f("fk_offering_core_code_offering_id_course_offering")),
                  nullable=False),
        sa.Column("course_id", sa.Uuid(),
                  sa.ForeignKey("course.id", ondelete="CASCADE",
                                name=op.f("fk_offering_core_code_course_id_course")),
                  nullable=False),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("effective", sa.String(16)),
        sa.Column("unit_code", sa.String(8)),
        sa.Column("source_id", sa.Uuid(),
                  sa.ForeignKey("data_source.id",
                                name=op.f("fk_offering_core_code_source_id_data_source")),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("offering_id", "code", name=op.f("uq_offering_core_code")),
    )
    op.create_index(op.f("ix_offering_core_code_offering_id"), "offering_core_code",
                    ["offering_id"])
    op.create_index(op.f("ix_offering_core_code_course_id"), "offering_core_code",
                    ["course_id"])
    op.create_index("ix_offering_core_code_term_course", "offering_core_code",
                    ["term_code", "course_id"])


def downgrade():
    bind = op.get_bind()
    stated = bind.execute(sa.text(
        "SELECT count(*) FROM student WHERE declaration_status IS NOT NULL")).scalar()
    if stated:
        raise RuntimeError(f"{stated} student(s) stated a declaration status; refusing to drop it.")
    op.drop_index("ix_offering_core_code_term_course", table_name="offering_core_code")
    op.drop_index(op.f("ix_offering_core_code_course_id"), table_name="offering_core_code")
    op.drop_index(op.f("ix_offering_core_code_offering_id"), table_name="offering_core_code")
    op.drop_table("offering_core_code")
    op.drop_constraint(op.f("ck_student_student_declaration_status_known"), "student",
                       type_="check")
    op.drop_column("student", "declaration_status")
