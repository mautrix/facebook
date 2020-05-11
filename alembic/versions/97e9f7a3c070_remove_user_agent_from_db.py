"""Remove user agent from db

Revision ID: 97e9f7a3c070
Revises: c56c9a30b228
Create Date: 2020-05-11 23:40:17.319060

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '97e9f7a3c070'
down_revision = 'c56c9a30b228'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column('user_agent')


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column('user_agent', sa.String(length=255), autoincrement=False, nullable=True))
