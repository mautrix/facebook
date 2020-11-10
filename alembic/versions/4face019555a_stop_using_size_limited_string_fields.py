"""Stop using size-limited string fields

Revision ID: 4face019555a
Revises: 3d4f3a252006
Create Date: 2020-11-10 01:43:55.557931

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4face019555a'
down_revision = '3d4f3a252006'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().engine.name != "postgresql":
        return

    op.alter_column("portal", "fbid", type_=sa.Text())
    op.alter_column("portal", "fb_receiver", type_=sa.Text())
    op.alter_column("portal", "mxid", type_=sa.Text())
    op.alter_column("portal", "avatar_url", type_=sa.Text())
    op.alter_column("portal", "name", type_=sa.Text())
    op.alter_column("portal", "photo_id", type_=sa.Text())

    op.alter_column("message", "mxid", type_=sa.Text())
    op.alter_column("message", "mx_room", type_=sa.Text())
    op.alter_column("message", "fbid", type_=sa.Text())
    op.alter_column("message", "fb_chat", type_=sa.Text())
    op.alter_column("message", "fb_receiver", type_=sa.Text())

    op.alter_column("puppet", "fbid", type_=sa.Text())
    op.alter_column("puppet", "name", type_=sa.Text())
    op.alter_column("puppet", "photo_id", type_=sa.Text())
    op.alter_column("puppet", "custom_mxid", type_=sa.Text())
    op.alter_column("puppet", "next_batch", type_=sa.Text())

    op.alter_column("reaction", "mxid", type_=sa.Text())
    op.alter_column("reaction", "mx_room", type_=sa.Text())
    op.alter_column("reaction", "fb_msgid", type_=sa.Text())
    op.alter_column("reaction", "fb_receiver", type_=sa.Text())
    op.alter_column("reaction", "fb_sender", type_=sa.Text())
    op.alter_column("reaction", "reaction", type_=sa.Text())

    op.alter_column("user", "mxid", type_=sa.Text())
    op.alter_column("user", "fbid", type_=sa.Text())
    op.alter_column("user", "notice_room", type_=sa.Text())
    op.alter_column("user", "user_agent", type_=sa.Text())
    op.alter_column("user", "fb_domain", type_=sa.Text())

    op.alter_column("user_portal", "user", type_=sa.Text())
    op.alter_column("user_portal", "portal", type_=sa.Text())
    op.alter_column("user_portal", "portal_receiver", type_=sa.Text())

    op.alter_column("contact", "user", type_=sa.Text())
    op.alter_column("contact", "contact", type_=sa.Text())

    op.alter_column("mx_user_profile", "room_id", type_=sa.Text())
    op.alter_column("mx_user_profile", "user_id", type_=sa.Text())
    op.alter_column("mx_user_profile", "displayname", type_=sa.Text())
    op.alter_column("mx_user_profile", "avatar_url", type_=sa.Text())
    op.alter_column("mx_room_state", "room_id", type_=sa.Text())


def downgrade():
    if op.get_bind().engine.name != "postgresql":
        return

    op.alter_column("portal", "fbid", type_=sa.String(127))
    op.alter_column("portal", "fb_receiver", type_=sa.String(127))
    op.alter_column("portal", "mxid", type_=sa.String(255))
    op.alter_column("portal", "avatar_url", type_=sa.String(255))
    op.alter_column("portal", "name", type_=sa.String(255))
    op.alter_column("portal", "photo_id", type_=sa.String(255))

    op.alter_column("message", "mxid", type_=sa.String(255))
    op.alter_column("message", "mx_room", type_=sa.String(255))
    op.alter_column("message", "fbid", type_=sa.String(127))
    op.alter_column("message", "fb_chat", type_=sa.String(127))
    op.alter_column("message", "fb_receiver", type_=sa.String(127))

    op.alter_column("puppet", "fbid", type_=sa.String(127))
    op.alter_column("puppet", "name", type_=sa.String(255))
    op.alter_column("puppet", "photo_id", type_=sa.String(255))
    op.alter_column("puppet", "custom_mxid", type_=sa.String(255))
    op.alter_column("puppet", "next_batch", type_=sa.String(255))

    op.alter_column("reaction", "mxid", type_=sa.String(255))
    op.alter_column("reaction", "mx_room", type_=sa.String(255))
    op.alter_column("reaction", "fb_msgid", type_=sa.String(127))
    op.alter_column("reaction", "fb_receiver", type_=sa.String(127))
    op.alter_column("reaction", "fb_sender", type_=sa.String(127))
    op.alter_column("reaction", "reaction", type_=sa.String(1))

    op.alter_column("user", "mxid", type_=sa.String(255))
    op.alter_column("user", "fbid", type_=sa.String(255))
    op.alter_column("user", "notice_room", type_=sa.String(255))
    op.alter_column("user", "user_agent", type_=sa.String(255))
    op.alter_column("user", "fb_domain", type_=sa.String(255))

    op.alter_column("user_portal", "user", type_=sa.String(255))
    op.alter_column("user_portal", "portal", type_=sa.String(255))
    op.alter_column("user_portal", "portal_receiver", type_=sa.String(255))

    op.alter_column("contact", "user", type_=sa.String(255))
    op.alter_column("contact", "contact", type_=sa.String(255))

    op.alter_column("mx_user_profile", "room_id", type_=sa.String(255))
    op.alter_column("mx_user_profile", "user_id", type_=sa.String(255))
    op.alter_column("mx_user_profile", "displayname", type_=sa.String())
    op.alter_column("mx_user_profile", "avatar_url", type_=sa.String(255))
    op.alter_column("mx_room_state", "room_id", type_=sa.String(255))
