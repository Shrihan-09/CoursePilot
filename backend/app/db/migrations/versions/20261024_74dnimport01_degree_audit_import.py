"""Degree Navigator audit import staging (Phase 7.4).

Revision ID: 74dnimport01; Revises: 73demand01.
Additive: three new tables and `transcript_import.source_document` (server
default 'rutgers_unofficial_transcript', so every existing import keeps its
meaning). The downgrade REFUSES while DN-sourced staging or a DN import
exists: those rows are a student's own record and are not dropped silently.
"""

import sqlalchemy as sa
from alembic import op

revision = "74dnimport01"
down_revision = "73demand01"
branch_labels = None
depends_on = None

_OPEN = "state IN ('processing','extracted')"


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ]


def upgrade():
    op.add_column("transcript_import", sa.Column(
        "source_document", sa.String(32), nullable=False,
        server_default="rutgers_unofficial_transcript"))

    op.create_table(
        "degree_audit_import",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_id", sa.Uuid(),
                  sa.ForeignKey("student.id", ondelete="CASCADE",
                                name=op.f("fk_degree_audit_import_student_id_student")),
                  nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("block_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("block_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("extractor_version", sa.String(32)),
        sa.Column("failure_code", sa.String(48)),
        sa.Column("storage_key", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("dn_subject", sa.String(255)),
        sa.Column("dn_version_label", sa.String(32)),
        sa.Column("dn_degree_id", sa.String(16)),
        sa.Column("dn_report_date", sa.String(32)),
        sa.Column("printed_at_text", sa.String(32)),
        sa.Column("completed_requirements_text", sa.String(32)),
        sa.Column("as_of", sa.DateTime(timezone=True)),
        sa.Column("as_of_source", sa.String(24)),
        sa.Column("mapping_key", sa.String(64)),
        sa.Column("mapping_version", sa.String(16)),
        sa.Column("track_check", sa.String(24)),
        sa.Column("track_message", sa.Text()),
        sa.Column("transcript_import_id", sa.Uuid(),
                  sa.ForeignKey("transcript_import.id", ondelete="SET NULL",
                                name=op.f("fk_degree_audit_import_transcript_import_id_"
                                          "transcript_import"))),
        sa.Column("extracted_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("discarded_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint(
            "state IN ('processing','extracted','failed','confirmed','discarded')",
            name=op.f("ck_degree_audit_import_state_known")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_degree_audit_import_version_positive")),
    )
    op.create_index("ix_degree_audit_import_student_id", "degree_audit_import", ["student_id"])
    op.create_index("uq_degree_audit_import_open_block", "degree_audit_import",
                    ["student_id", "content_sha256", "block_index"], unique=True,
                    postgresql_where=sa.text(_OPEN), sqlite_where=sa.text(_OPEN))

    op.create_table(
        "degree_audit_requirement_row",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("import_id", sa.Uuid(),
                  sa.ForeignKey("degree_audit_import.id", ondelete="CASCADE",
                                name=op.f("fk_degree_audit_requirement_row_import_id_"
                                          "degree_audit_import")),
                  nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("parent_row_index", sa.Integer()),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("dn_number", sa.Integer()),
        sa.Column("dn_heading", sa.Text()),
        sa.Column("dn_code", sa.String(16)),
        sa.Column("extracted_title", sa.Text()),
        sa.Column("extracted_codes", sa.JSON(), nullable=False),
        sa.Column("extracted_icon", sa.String(32)),
        sa.Column("extracted_total", sa.Integer()),
        sa.Column("extracted_completed", sa.Integer()),
        sa.Column("extracted_needs", sa.Text()),
        sa.Column("extracted_courses", sa.JSON(), nullable=False),
        sa.Column("extraction_problems", sa.JSON(), nullable=False),
        sa.Column("requirement_code", sa.String(64)),
        sa.Column("mapping_review_status", sa.String(24)),
        sa.Column("dn_status", sa.String(24), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reconciliation", sa.String(24)),
        sa.Column("cp_status", sa.String(32)),
        sa.Column("cp_courses", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("import_id", "row_index", name="uq_dn_requirement_row_index"),
        sa.CheckConstraint("kind IN ('requirement','goal')",
                           name=op.f("ck_degree_audit_requirement_row_kind_known")),
        sa.CheckConstraint("state IN ('matched','needs_review','unmapped')",
                           name=op.f("ck_degree_audit_requirement_row_state_known")),
        sa.CheckConstraint("dn_status IN ('satisfied','in_progress_or_planned','not_satisfied',"
                           "'unknown')",
                           name=op.f("ck_degree_audit_requirement_row_dn_status_known")),
    )
    op.create_index("ix_degree_audit_requirement_row_import_id",
                    "degree_audit_requirement_row", ["import_id"])

    op.create_table(
        "degree_audit_course_row",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("import_id", sa.Uuid(),
                  sa.ForeignKey("degree_audit_import.id", ondelete="CASCADE",
                                name=op.f("fk_degree_audit_course_row_import_id_"
                                          "degree_audit_import")),
                  nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("list_kind", sa.String(24), nullable=False),
        sa.Column("requirement_number", sa.Integer()),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("extracted_course_code", sa.String(16)),
        sa.Column("extracted_credits", sa.String(8)),
        sa.Column("extracted_term", sa.String(32)),
        sa.Column("extracted_grade", sa.String(16)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("extraction_problems", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("transcript_row_id", sa.Uuid(),
                  sa.ForeignKey("transcript_import_row.id", ondelete="SET NULL",
                                name=op.f("fk_degree_audit_course_row_transcript_row_id_"
                                          "transcript_import_row"))),
        *timestamps(),
        sa.UniqueConstraint("import_id", "row_index", name="uq_dn_course_row_index"),
        sa.CheckConstraint("state IN ('matched','needs_review','info')",
                           name=op.f("ck_degree_audit_course_row_state_known")),
    )
    op.create_index("ix_degree_audit_course_row_import_id", "degree_audit_course_row",
                    ["import_id"])


def downgrade():
    bind = op.get_bind()
    held = bind.execute(sa.text(
        "SELECT (SELECT count(*) FROM degree_audit_import) + "
        "(SELECT count(*) FROM transcript_import "
        " WHERE source_document <> 'rutgers_unofficial_transcript')")).scalar()
    if held:
        raise RuntimeError(
            f"Refusing to downgrade 74dnimport01: {held} Degree Navigator import row(s) exist. "
            "They are students' own records; export or discard them first.")
    op.drop_index("ix_degree_audit_course_row_import_id", table_name="degree_audit_course_row")
    op.drop_table("degree_audit_course_row")
    op.drop_index("ix_degree_audit_requirement_row_import_id",
                  table_name="degree_audit_requirement_row")
    op.drop_table("degree_audit_requirement_row")
    op.drop_index("uq_degree_audit_import_open_block", table_name="degree_audit_import")
    op.drop_index("ix_degree_audit_import_student_id", table_name="degree_audit_import")
    op.drop_table("degree_audit_import")
    op.drop_column("transcript_import", "source_document")
