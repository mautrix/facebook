from mautrix.util.async_db import Database

from .upgrade import upgrade_table
from .message import Message
from .reaction import Reaction
from .portal import Portal, ThreadType
from .puppet import Puppet
from .user import User
from .user_portal import UserPortal
from .user_contact import UserContact


def init(db: Database) -> None:
    for table in (Portal, Message, Reaction, User, Puppet, UserPortal, UserContact):
        table.db = db


__all__ = ["upgrade_table", "init", "Message", "Reaction", "Portal", "ThreadType", "Puppet",
           "User", "UserPortal", "UserContact"]
