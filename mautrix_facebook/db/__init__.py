from .base import Base
from .message import Message
from .portal import Portal
from .puppet import Puppet
from .user import User
from .mx_room_state import RoomState
from .mx_user_profile import UserProfile


def init(db_engine) -> None:
    for table in Portal, Message, User, Puppet, UserProfile, RoomState:
        table.db = db_engine
        table.t = table.__table__
        table.c = table.t.c
