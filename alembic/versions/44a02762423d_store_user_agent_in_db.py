"""Store user agent in db

Revision ID: 44a02762423d
Revises: 3355b59b191c
Create Date: 2019-08-20 18:43:49.065177

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '44a02762423d'
down_revision = '3355b59b191c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("user_agent", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("user_agent")
