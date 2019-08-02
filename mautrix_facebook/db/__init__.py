from mautrix.bridge.db import RoomState, UserProfile

from .message import Message
from .reaction import Reaction
from .portal import Portal
from .puppet import Puppet
from .user import User


def init(db_engine) -> None:
    for table in Portal, Message, Reaction, User, Puppet, UserProfile, RoomState:
        table.db = db_engine
        table.t = table.__table__
        table.c = table.t.c
