"""Add profile set booleans to puppet table

Revision ID: f91274813e8c
Revises: 4face019555a
Create Date: 2020-11-10 02:11:41.981068

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f91274813e8c'
down_revision = '4face019555a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('puppet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('avatar_set', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('name_set', sa.Boolean(), server_default=sa.false(), nullable=False))

    # Fill the database with assumed data so the bridge doesn't spam a ton of no-op profile updates
    op.execute("UPDATE puppet SET name_set=true WHERE name<>''")
    op.execute("UPDATE puppet SET avatar_set=true WHERE photo_id<>''")


def downgrade():
    with op.batch_alter_table('puppet', schema=None) as batch_op:
        batch_op.drop_column('name_set')
        batch_op.drop_column('avatar_set')
