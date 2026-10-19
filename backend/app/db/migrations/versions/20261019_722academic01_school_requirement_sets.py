"""Independent school requirement sets and scoped eligibility.

Revision ID: 722academic01
Revises: b491ce3a2784

Schema-only, additive migration. Existing program-owned trees remain intact.
School definitions are loaded by the idempotent Core ingestion pipeline; no
student record or saved plan is rewritten. Engine/fingerprint changes mark
old cached and projected results stale.
"""

import sqlalchemy as sa
from alembic import op

revision = "722academic01"
down_revision = "b491ce3a2784"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_requirement_set_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), sa.ForeignKey("school.id"), nullable=False),
        sa.Column("catalog_year", sa.String(16), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("definition_version", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_prose", sa.Text()),
        sa.Column("definition_sha256", sa.String(64), nullable=False),
        sa.Column("lifecycle_state", sa.String(24), nullable=False),
        sa.Column("sharing_policy", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("data_source.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "school_id", "catalog_year", "code", name="uq_school_requirement_set_version"
        ),
    )
    op.create_index(
        "ix_school_requirement_set_version_school_id",
        "school_requirement_set_version",
        ["school_id"],
    )
    op.alter_column("requirement", "program_version_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("requirement", sa.Column("school_requirement_set_version_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_requirement_school_set",
        "requirement",
        "school_requirement_set_version",
        ["school_requirement_set_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_requirement_school_requirement_set_version_id",
        "requirement",
        ["school_requirement_set_version_id"],
    )
    op.add_column("requirement", sa.Column("allocation_scope", sa.String(64)))
    op.add_column("requirement", sa.Column("eligible_origins", sa.JSON()))
    op.add_column(
        "requirement",
        sa.Column("graded_only", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("requirement", sa.Column("required_campus", sa.String(8)))
    op.add_column("requirement", sa.Column("exemption_rule", sa.JSON()))
    op.create_table(
        "student_academic_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "student_id", sa.Uuid(), sa.ForeignKey("student.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("evidence_key", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('exam_score','attendance_campus','requirement_waiver')",
            name="academic_evidence_kind_known",
        ),
    )
    op.create_index(
        "ix_student_academic_evidence_student_id", "student_academic_evidence", ["student_id"]
    )
    op.create_unique_constraint(
        "uq_school_requirement_code", "requirement", ["school_requirement_set_version_id", "code"]
    )
    op.create_check_constraint(
        "requirement_exactly_one_owner",
        "requirement",
        "(program_version_id IS NOT NULL AND school_requirement_set_version_id IS NULL) OR "
        "(program_version_id IS NULL AND school_requirement_set_version_id IS NOT NULL)",
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER trg_rules_version_bump AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE "
            "ON school_requirement_set_version FOR EACH STATEMENT "
            "EXECUTE FUNCTION bump_rules_version()"
        )


def downgrade():
    # Do not silently delete independently loaded academic definitions.
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM requirement "
                "WHERE school_requirement_set_version_id IS NOT NULL"
            )
        )
        .scalar()
    )
    evidence_count = (
        op.get_bind().execute(sa.text("SELECT COUNT(*) FROM student_academic_evidence")).scalar()
    )
    if count or evidence_count:
        raise RuntimeError(
            "Export or explicitly reconcile academic definitions/evidence before downgrade"
        )
    op.drop_constraint("requirement_exactly_one_owner", "requirement", type_="check")
    op.drop_constraint("uq_school_requirement_code", "requirement", type_="unique")
    op.drop_index("ix_requirement_school_requirement_set_version_id", table_name="requirement")
    op.drop_constraint("fk_requirement_school_set", "requirement", type_="foreignkey")
    op.drop_table("student_academic_evidence")
    for column in (
        "exemption_rule",
        "required_campus",
        "graded_only",
        "eligible_origins",
        "allocation_scope",
        "school_requirement_set_version_id",
    ):
        op.drop_column("requirement", column)
    op.alter_column("requirement", "program_version_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("school_requirement_set_version")
