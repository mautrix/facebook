"""Add fb_receiver to Message

Revision ID: 3c5af010538a
Revises: c36b294b1f5f
Create Date: 2019-05-01 19:51:32.891102

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3c5af010538a'
down_revision = 'c36b294b1f5f'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('message')
    op.create_table('message',
                    sa.Column('mxid', sa.String(length=255), nullable=True),
                    sa.Column('mx_room', sa.String(length=255), nullable=True),
                    sa.Column('fbid', sa.String(length=127), nullable=False),
                    sa.Column('fb_receiver', sa.String(length=127), nullable=False),
                    sa.Column('index', sa.SmallInteger(), nullable=False),
                    sa.PrimaryKeyConstraint('fbid', 'fb_receiver', 'index'),
                    sa.UniqueConstraint('mxid', 'mx_room', name='_mx_id_room'))


def downgrade():
    op.drop_table('message')
    op.create_table('message',
                    sa.Column('mxid', sa.String(length=255), nullable=True),
                    sa.Column('mx_room', sa.String(length=255), nullable=True),
                    sa.Column('fbid', sa.String(length=127), nullable=False),
                    sa.Column('index', sa.SmallInteger(), nullable=False),
                    sa.PrimaryKeyConstraint('fbid', 'index'),
                    sa.UniqueConstraint('mxid', 'mx_room', name='_mx_id_room'))
