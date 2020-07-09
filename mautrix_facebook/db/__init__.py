from mautrix.bridge.db import RoomState, UserProfile

from .message import Message
from .reaction import Reaction
from .portal import Portal, ThreadType
from .puppet import Puppet
from .user import User
from .user_portal import UserPortal
from .user_contact import Contact


def init(db_engine) -> None:
    for table in (Portal, Message, Reaction, User, Puppet, UserPortal, Contact, UserProfile,
                  RoomState):
        table.db = db_engine
        table.t = table.__table__
        table.c = table.t.c
        table.column_names = table.c.keys()
