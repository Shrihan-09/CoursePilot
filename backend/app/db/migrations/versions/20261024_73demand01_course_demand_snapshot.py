"""Daily course demand snapshot (Phase 7.3 demand collection).

Revision ID: 73demand01; Revises: 73gradterm01.
Additive. Every row is derived from `section_availability_change` and
`availability_refresh` (which are unchanged), so the downgrade drops the
table without losing an observation: a re-run of the rollup rebuilds it.
"""

import sqlalchemy as sa
from alembic import op

revision = "73demand01"
down_revision = "73gradterm01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "course_demand_snapshot",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("course_id", sa.Uuid(),
                  sa.ForeignKey("course.id", ondelete="CASCADE",
                                name=op.f("fk_course_demand_snapshot_course_id_course")),
                  nullable=False),
        sa.Column("course_string", sa.String(32), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("insufficient_reason", sa.String(64)),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("observed_count", sa.Integer()),
        sa.Column("open_count", sa.Integer()),
        sa.Column("closed_count", sa.Integer()),
        sa.Column("closed_share", sa.Numeric(5, 4)),
        sa.Column("first_closed", sa.JSON(), nullable=False),
        sa.Column("window_opens_at", sa.DateTime(timezone=True)),
        sa.Column("refreshes", sa.Integer(), nullable=False),
        sa.Column("max_refresh_gap_seconds", sa.Integer()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('ok','insufficient_data')",
                           name=op.f("ck_course_demand_snapshot_demand_status_known")),
        sa.CheckConstraint("status = 'ok' OR closed_share IS NULL",
                           name=op.f("ck_course_demand_snapshot_insufficient_not_imputed")),
        sa.UniqueConstraint("term_code", "course_id", "snapshot_date",
                            name="uq_demand_term_course_day"),
    )
    op.create_index("ix_demand_term_day", "course_demand_snapshot",
                    ["term_code", "snapshot_date"])


def downgrade():
    op.drop_index("ix_demand_term_day", table_name="course_demand_snapshot")
    op.drop_table("course_demand_snapshot")
