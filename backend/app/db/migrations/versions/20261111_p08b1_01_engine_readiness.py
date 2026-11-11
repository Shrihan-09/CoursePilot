"""Program expansion engine readiness: components, stages, class-year curricula,
school rules, the course knowledge index and requirement reports (Prompt 08 B1).

Revision ID: p08b101; Revises: p11b02.

Additive. Every existing row keeps its meaning:
  * every existing requirement set becomes kind='gen_ed', composition='school'
    (exactly how it is composed today);
  * every program version gets curriculum_key='' (one curriculum per year);
  * every program rule keeps its program owner.
No student record or saved plan is rewritten. The knowledge index starts
empty; `python -m app.cli.knowledge rebuild` builds it.
"""
import sqlalchemy as sa
from alembic import op

revision = "p08b101"
down_revision = "p11b02"
branch_labels = None
depends_on = None

_TS = (
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)


def _ts():
    return [c.copy() for c in _TS]


def upgrade():
    # --- components ---------------------------------------------------------
    op.add_column("school_requirement_set_version", sa.Column(
        "kind", sa.String(24), nullable=False, server_default="gen_ed"))
    op.add_column("school_requirement_set_version", sa.Column(
        "composition", sa.String(16), nullable=False, server_default="school"))
    op.add_column("school_requirement_set_version", sa.Column("applicability", sa.JSON()))
    op.add_column("school_requirement_set_version", sa.Column("stage", sa.JSON()))
    op.add_column("school_requirement_set_version", sa.Column("degree_credits_min", sa.Numeric(5, 1)))
    op.add_column("school_requirement_set_version", sa.Column("degree_credits_source", sa.JSON()))
    op.create_check_constraint(
        op.f("ck_school_requirement_set_version_component_kind_known"),
        "school_requirement_set_version",
        "kind IN ('gen_ed','school_core','entry_stage','shared_major')")
    op.create_check_constraint(
        op.f("ck_school_requirement_set_version_component_composition_known"),
        "school_requirement_set_version", "composition IN ('school','linked')")
    op.create_check_constraint(
        op.f("ck_school_requirement_set_version_entry_stage_names_its_stage"),
        "school_requirement_set_version", "kind <> 'entry_stage' OR stage IS NOT NULL")
    op.create_table(
        "program_version_component",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("program_version_id", sa.Uuid(),
                  sa.ForeignKey("program_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_id", sa.Uuid(),
                  sa.ForeignKey("school_requirement_set_version.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        *_ts(),
        sa.UniqueConstraint("program_version_id", "component_id",
                            name="uq_program_version_component"),
    )
    op.create_index("ix_program_version_component_program_version_id",
                    "program_version_component", ["program_version_id"])
    op.create_index("ix_program_version_component_component_id",
                    "program_version_component", ["component_id"])

    # --- program versions: class-year curricula, stages, totals -------------
    op.add_column("program_version", sa.Column(
        "curriculum_key", sa.String(48), nullable=False, server_default=""))
    op.add_column("program_version", sa.Column("curriculum_class_years", sa.JSON()))
    op.add_column("program_version", sa.Column("admission_stage", sa.String(48)))
    op.add_column("program_version", sa.Column("degree_credits_min", sa.Numeric(5, 1)))
    op.add_column("program_version", sa.Column("degree_credits_source", sa.JSON()))
    op.drop_constraint("uq_program_version", "program_version", type_="unique")
    op.create_unique_constraint("uq_program_version", "program_version",
                                ["program_id", "catalog_year", "curriculum_key"])

    # --- requirement nodes --------------------------------------------------
    op.add_column("requirement", sa.Column("available_after_stage", sa.String(48)))
    op.add_column("requirement", sa.Column("min_course_credits", sa.Numeric(4, 1)))
    op.add_column("requirement", sa.Column("certification_codes", sa.JSON()))
    op.add_column("requirement", sa.Column("group_caps", sa.JSON()))
    op.add_column("requirement", sa.Column("unresolved_conflicts", sa.JSON()))

    # --- school-level rules -------------------------------------------------
    op.alter_column("program_rule", "program_version_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("program_rule", sa.Column("school_requirement_set_version_id", sa.Uuid()))
    op.create_foreign_key("fk_program_rule_school_set", "program_rule",
                          "school_requirement_set_version",
                          ["school_requirement_set_version_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_program_rule_school_requirement_set_version_id", "program_rule",
                    ["school_requirement_set_version_id"])
    op.create_unique_constraint("uq_school_rule_code", "program_rule",
                                ["school_requirement_set_version_id", "code"])
    op.create_check_constraint(
        op.f("ck_program_rule_program_rule_exactly_one_owner"), "program_rule",
        "(program_version_id IS NOT NULL AND school_requirement_set_version_id IS NULL) OR "
        "(program_version_id IS NULL AND school_requirement_set_version_id IS NOT NULL)")
    op.drop_constraint(op.f("ck_program_rule_program_rule_type_known"), "program_rule", type_="check")
    op.create_check_constraint(
        op.f("ck_program_rule_program_rule_type_known"), "program_rule",
        "rule_type IN ('max_grade_count','course_exclusion','residency','min_gpa',"
        "'residency_window','pass_fail_limit','double_count_limit')")

    # --- verified admission and class-year evidence ------------------------
    op.drop_constraint(op.f("ck_student_academic_evidence_academic_evidence_kind_known"),
                       "student_academic_evidence", type_="check")
    op.create_check_constraint(
        op.f("ck_student_academic_evidence_academic_evidence_kind_known"),
        "student_academic_evidence",
        "evidence_kind IN ('exam_score','attendance_campus','requirement_waiver',"
        "'admission','curriculum_class_year')")

    # --- course knowledge index ---------------------------------------------
    op.create_table(
        "knowledge_build",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("builder_version", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("counts", sa.JSON()),
        sa.Column("error", sa.Text()),
        *_ts(),
        sa.CheckConstraint("state IN ('building','ready','failed')",
                           name=op.f("ck_knowledge_build_knowledge_build_state_known")),
    )
    op.create_index("ix_knowledge_build_manifest_sha256", "knowledge_build", ["manifest_sha256"])
    op.create_table(
        "knowledge_membership",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("build_id", sa.Uuid(), sa.ForeignKey("knowledge_build.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("course.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("course_string", sa.String(32), nullable=False),
        sa.Column("owner_kind", sa.String(24), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_key", sa.String(96), nullable=False),
        sa.Column("requirement_id", sa.Uuid(),
                  sa.ForeignKey("requirement.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_code", sa.String(64), nullable=False),
        sa.Column("requirement_name", sa.Text(), nullable=False),
        sa.Column("requirement_system", sa.String(32), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("basis", sa.String(24), nullable=False),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("certified_term", sa.String(16)),
        sa.Column("lifecycle_state", sa.String(24), nullable=False),
        sa.Column("conflicts", sa.JSON()),
        sa.Column("available_after_stage", sa.String(48)),
        sa.Column("min_grade", sa.String(4)),
        sa.UniqueConstraint("build_id", "term_code", "course_id", "requirement_id", "category",
                            name="uq_knowledge_membership"),
        sa.CheckConstraint("owner_kind IN ('program_version','component')",
                           name=op.f("ck_knowledge_membership_knowledge_owner_kind_known")),
        sa.CheckConstraint("basis IN ('curated_list','curated_query','term_certification')",
                           name=op.f("ck_knowledge_membership_knowledge_basis_known")),
    )
    op.create_index("ix_knowledge_membership_lookup", "knowledge_membership",
                    ["build_id", "term_code", "course_id"])
    op.create_index("ix_knowledge_membership_owner", "knowledge_membership",
                    ["build_id", "owner_id", "term_code"])
    op.create_table(
        "knowledge_context",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("build_id", sa.Uuid(), sa.ForeignKey("knowledge_build.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("program_version_id", sa.Uuid(),
                  sa.ForeignKey("program_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("program_key", sa.String(96), nullable=False),
        sa.Column("owner_kind", sa.String(24), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_key", sa.String(96), nullable=False),
        sa.Column("via", sa.String(24), nullable=False),
        sa.UniqueConstraint("build_id", "program_version_id", "owner_id",
                            name="uq_knowledge_context"),
    )
    op.create_index("ix_knowledge_context_build_id", "knowledge_context", ["build_id"])
    op.create_table(
        "knowledge_coverage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("build_id", sa.Uuid(), sa.ForeignKey("knowledge_build.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("program_version_id", sa.Uuid(),
                  sa.ForeignKey("program_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("program_key", sa.String(96), nullable=False),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("offered_courses", sa.Integer(), nullable=False),
        sa.Column("mapped_courses", sa.Integer(), nullable=False),
        sa.Column("excluded_courses", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("incomplete_reasons", sa.JSON(), nullable=False),
        sa.UniqueConstraint("build_id", "program_version_id", "term_code",
                            name="uq_knowledge_coverage"),
    )
    op.create_index("ix_knowledge_coverage_build_id", "knowledge_coverage", ["build_id"])

    # --- "report a wrong requirement" ---------------------------------------
    op.create_table(
        "requirement_report",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("user_account.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("program_key", sa.String(96), nullable=False),
        sa.Column("catalog_year", sa.String(16), nullable=False),
        sa.Column("requirement_code", sa.String(64), nullable=False),
        sa.Column("requirement_name", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_note", sa.Text()),
        *_ts(),
        sa.CheckConstraint("status IN ('open','resolved','dismissed')",
                           name=op.f("ck_requirement_report_requirement_report_status")),
        sa.CheckConstraint("length(message) BETWEEN 1 AND 2000",
                           name=op.f("ck_requirement_report_requirement_report_message_length")),
    )
    op.create_index("ix_requirement_report_account_id", "requirement_report", ["account_id"])
    op.create_index("ix_requirement_report_status_created", "requirement_report",
                    ["status", "created_at"])


def downgrade():
    op.drop_table("requirement_report")
    op.drop_table("knowledge_coverage")
    op.drop_table("knowledge_context")
    op.drop_table("knowledge_membership")
    op.drop_table("knowledge_build")
    op.drop_constraint(op.f("ck_student_academic_evidence_academic_evidence_kind_known"),
                       "student_academic_evidence", type_="check")
    op.execute("DELETE FROM student_academic_evidence "
               "WHERE evidence_kind IN ('admission','curriculum_class_year')")
    op.create_check_constraint(
        op.f("ck_student_academic_evidence_academic_evidence_kind_known"),
        "student_academic_evidence",
        "evidence_kind IN ('exam_score','attendance_campus','requirement_waiver')")
    op.drop_constraint(op.f("ck_program_rule_program_rule_type_known"), "program_rule", type_="check")
    op.execute("DELETE FROM program_rule WHERE school_requirement_set_version_id IS NOT NULL "
               "OR rule_type IN ('residency_window','pass_fail_limit','double_count_limit')")
    op.create_check_constraint(
        op.f("ck_program_rule_program_rule_type_known"), "program_rule",
        "rule_type IN ('max_grade_count','course_exclusion','residency','min_gpa')")
    op.drop_constraint(op.f("ck_program_rule_program_rule_exactly_one_owner"), "program_rule",
                       type_="check")
    op.drop_constraint("uq_school_rule_code", "program_rule", type_="unique")
    op.drop_index("ix_program_rule_school_requirement_set_version_id", "program_rule")
    op.drop_constraint("fk_program_rule_school_set", "program_rule", type_="foreignkey")
    op.drop_column("program_rule", "school_requirement_set_version_id")
    op.alter_column("program_rule", "program_version_id", existing_type=sa.Uuid(), nullable=False)
    for col in ("unresolved_conflicts", "group_caps", "certification_codes",
                "min_course_credits", "available_after_stage"):
        op.drop_column("requirement", col)
    op.execute("DELETE FROM program_version WHERE curriculum_key <> ''")
    op.drop_constraint("uq_program_version", "program_version", type_="unique")
    op.create_unique_constraint("uq_program_version", "program_version",
                                ["program_id", "catalog_year"])
    for col in ("degree_credits_source", "degree_credits_min", "admission_stage", "curriculum_class_years",
                "curriculum_key"):
        op.drop_column("program_version", col)
    op.drop_table("program_version_component")
    for name in ("entry_stage_names_its_stage", "component_composition_known",
                 "component_kind_known"):
        op.drop_constraint(op.f(f"ck_school_requirement_set_version_{name}"),
                           "school_requirement_set_version", type_="check")
    for col in ("degree_credits_source", "degree_credits_min", "stage", "applicability", "composition", "kind"):
        op.drop_column("school_requirement_set_version", col)
