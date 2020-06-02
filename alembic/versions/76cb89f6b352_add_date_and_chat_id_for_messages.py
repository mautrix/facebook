"""Add date and chat ID for messages

Revision ID: 76cb89f6b352
Revises: a26da6af5cff
Create Date: 2020-06-02 14:44:13.752450

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "76cb89f6b352"
down_revision = "a26da6af5cff"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("message") as batch_op:
        batch_op.add_column(sa.Column("date", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("fb_chat", sa.String(127), nullable=True))
        batch_op.alter_column("mx_room", existing_type=sa.String(255), nullable=False)
        batch_op.alter_column("mxid", existing_type=sa.String(255), nullable=False)


def downgrade():
    with op.batch_alter_table("message") as batch_op:
        batch_op.alter_column("mxid", existing_type=sa.String(255), nullable=True)
        batch_op.alter_column("mx_room", existing_type=sa.String(255), nullable=True)
        batch_op.drop_column("fb_chat")
        batch_op.drop_column("date")
