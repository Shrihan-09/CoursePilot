"""Staged schedule imports and persistent confirmed registration identities.
Revision ID: 722schedule01; Revises: 722academic01.
No academic history rows are modified. Downgrade REFUSES while confirmed
registrations or multi-component current sections exist (export them first);
staged imports are disposable.
"""

import sqlalchemy as sa
from alembic import op

revision = "722schedule01"
down_revision = "722academic01"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade():
    op.create_table(
        "schedule_import",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "student_id", sa.Uuid(), sa.ForeignKey("student.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("rows", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_schedule_import_student_id", "schedule_import", ["student_id"])
    op.create_table(
        "student_registration",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "student_id", sa.Uuid(), sa.ForeignKey("student.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("course_string", sa.String(32), nullable=False),
        sa.Column("section_number", sa.String(16), nullable=False),
        sa.Column("supplement_code", sa.String(16), nullable=False, server_default=""),
        sa.Column("index_number", sa.String(16)),
        sa.Column("section_id", sa.Uuid(), sa.ForeignKey("course_section.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("import_id", sa.Uuid(), sa.ForeignKey("schedule_import.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.UniqueConstraint(
            "student_id",
            "term_code",
            "course_string",
            "section_number",
            "supplement_code",
            name="uq_registration_identity",
        ),
    )
    op.create_index("ix_student_registration_student_id", "student_registration", ["student_id"])
    op.add_column("student_current_section", sa.Column("section_number", sa.String(16)))
    op.add_column(
        "student_current_section",
        sa.Column("supplement_code", sa.String(16), nullable=False, server_default=""),
    )
    op.execute(
        "UPDATE student_current_section AS c SET section_number = s.section_number "
        "FROM course_section AS s WHERE c.section_id = s.id"
    )
    op.execute(
        "UPDATE student_current_section AS c SET supplement_code = co.supplement_code "
        "FROM course_section AS s JOIN course_offering AS o ON s.offering_id = o.id "
        "JOIN course AS co ON o.course_id = co.id WHERE c.section_id = s.id"
    )
    op.drop_constraint(
        "uq_student_current_section_course_component", "student_current_section", type_="unique"
    )
    op.create_unique_constraint(
        "uq_student_current_section_course_component",
        "student_current_section",
        ["student_course_id", "component", "supplement_code"],
    )


def downgrade():
    # Refuse rather than destroy student-confirmed data (the 722academic01
    # precedent): confirmed registrations, and current-section rows that the
    # older (student_course_id, component) uniqueness could not hold.
    bind = op.get_bind()
    registrations = bind.execute(sa.text("SELECT COUNT(*) FROM student_registration")).scalar()
    collisions = bind.execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT student_course_id, component FROM student_current_section "
        "GROUP BY student_course_id, component HAVING COUNT(*) > 1) AS c")).scalar()
    if registrations or collisions:
        raise RuntimeError(
            "Export or explicitly reconcile confirmed registrations and multi-component "
            "current sections before downgrading 722schedule01")
    op.drop_constraint(
        "uq_student_current_section_course_component", "student_current_section", type_="unique"
    )
    op.create_unique_constraint(
        "uq_student_current_section_course_component",
        "student_current_section",
        ["student_course_id", "component"],
    )
    op.drop_column("student_current_section", "supplement_code")
    op.drop_column("student_current_section", "section_number")
    op.drop_table("student_registration")
    op.drop_table("schedule_import")
