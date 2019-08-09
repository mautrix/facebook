"""Store custom puppet next_batch in database

Revision ID: 8e0f1142c8d5
Revises: 1a1ea46dc3e1
Create Date: 2019-08-09 23:04:40.838488

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8e0f1142c8d5"
down_revision = "1a1ea46dc3e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.add_column(sa.Column("next_batch", sa.String(255), nullable=True))


def downgrade():
    with op.batch_alter_table("puppet") as batch_op:
        batch_op.drop_column("next_batch")
