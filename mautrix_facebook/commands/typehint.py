from typing import TYPE_CHECKING

from mautrix.bridge.commands import CommandEvent as BaseCommandEvent

if TYPE_CHECKING:
    from ..__main__ import MessengerBridge
    from ..user import User


class CommandEvent(BaseCommandEvent):
    bridge: "MessengerBridge"
    sender: "User"
