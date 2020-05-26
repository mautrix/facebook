"""Add bridge notice room column for users

Revision ID: a26da6af5cff
Revises: 97e9f7a3c070
Create Date: 2020-05-26 15:44:47.460965

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a26da6af5cff'
down_revision = '97e9f7a3c070'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column('notice_room', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column('notice_room')
