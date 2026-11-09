"""Assistant conversation titles, archive flag and action ownership.

Revision ID: p11b02; Revises: p11b01.
Existing messages stay in their conversation; legacy histories are consolidated
per student before multiple conversations become available. No message is removed.
"""
import sqlalchemy as sa
from alembic import op

revision = "p11b02"
down_revision = "p11b01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assistant_conversation", sa.Column("title", sa.String(160),
                                                    nullable=False, server_default="New chat"))
    op.add_column("assistant_conversation", sa.Column("archived", sa.Boolean(),
                                                    nullable=False, server_default=sa.false()))
    op.add_column("assistant_action", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_assistant_action_conversation", "assistant_action",
                          "assistant_conversation", ["conversation_id"], ["id"], ondelete="CASCADE")
    # Preserve the oldest conversation id and move every legacy message into it.
    op.execute("""WITH owners AS (
        SELECT DISTINCT ON (student_id) student_id, id FROM assistant_conversation
        ORDER BY student_id, created_at, id)
        UPDATE assistant_message m SET conversation_id=o.id FROM owners o
        WHERE m.student_id=o.student_id""")
    op.execute("""WITH owners AS (
        SELECT DISTINCT ON (student_id) student_id, id FROM assistant_conversation
        ORDER BY student_id, created_at, id)
        UPDATE assistant_action a SET conversation_id=o.id FROM owners o
        WHERE a.student_id=o.student_id""")
    op.execute("""DELETE FROM assistant_conversation c WHERE NOT EXISTS
        (SELECT 1 FROM assistant_message m WHERE m.conversation_id=c.id)
        AND NOT EXISTS (SELECT 1 FROM assistant_action a WHERE a.conversation_id=c.id)
        AND c.id <> (SELECT x.id FROM assistant_conversation x WHERE x.student_id=c.student_id
                     ORDER BY x.created_at,x.id LIMIT 1)""")
    op.execute("""UPDATE assistant_conversation c SET title=COALESCE((
        SELECT left(array_to_string((regexp_split_to_array(trim(m.content), '\\s+'))[1:6], ' '),160)
        FROM assistant_message m WHERE m.conversation_id=c.id AND m.role='user'
        ORDER BY m.created_at,m.id LIMIT 1), 'New chat'),
        updated_at=COALESCE((SELECT max(m.created_at) FROM assistant_message m
                           WHERE m.conversation_id=c.id),c.updated_at)""")


def downgrade():
    op.drop_constraint("fk_assistant_action_conversation", "assistant_action", type_="foreignkey")
    op.drop_column("assistant_action", "conversation_id")
    op.drop_column("assistant_conversation", "archived")
    op.drop_column("assistant_conversation", "title")
