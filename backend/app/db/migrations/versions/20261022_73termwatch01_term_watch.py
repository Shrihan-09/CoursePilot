"""Term watcher state: fetch observations and evidence-backed coverage promotion.

Revision ID: 73termwatch01; Revises: 724currentterm01.
Additive; the columns describe public Rutgers term data only, so the
downgrade simply drops them (term_watch_cli rebuilds them).
"""

import sqlalchemy as sa
from alembic import op

revision = "73termwatch01"
down_revision = "724currentterm01"
branch_labels = None
depends_on = None


def upgrade():
    # The '[]' default only fills existing rows; the application supplies the
    # value afterwards (a JSON server default cannot be compared by alembic).
    op.add_column("rutgers_term", sa.Column("observations", sa.JSON(), nullable=False,
                                            server_default=sa.text("'[]'")))
    op.alter_column("rutgers_term", "observations", server_default=None)
    op.add_column("rutgers_term", sa.Column("coverage_state", sa.String(16), nullable=False,
                                            server_default="partial"))
    op.add_column("rutgers_term", sa.Column("coverage_evidence", sa.String(256)))
    op.add_column("rutgers_term", sa.Column("coverage_promoted_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("rutgers_term", "coverage_promoted_at")
    op.drop_column("rutgers_term", "coverage_evidence")
    op.drop_column("rutgers_term", "coverage_state")
    op.drop_column("rutgers_term", "observations")
