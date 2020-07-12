from mautrix.client.state_store.sqlalchemy import RoomState, UserProfile

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
        table.bind(db_engine)
