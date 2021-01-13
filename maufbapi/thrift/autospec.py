# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2021 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from typing import Tuple, Union, Optional, Dict, Type, NamedTuple, Any, TYPE_CHECKING
import sys

import attr

from .type import TType

Subtype = Union[None, TType, Tuple['Subtype', 'Subtype']]


class RecursiveType(NamedTuple):
    type: TType
    python_type: Optional[Type['Any']] = None
    item_type: Optional['RecursiveType'] = None
    key_type: Optional[TType] = None
    value_type: Optional['RecursiveType'] = None


class ThriftField(NamedTuple):
    name: str
    rtype: RecursiveType


if TYPE_CHECKING:
    from typing import Protocol


    class ThriftObject(Protocol):
        thrift_spec: Dict[int, ThriftField]
        thrift_spec_by_type: Dict[Tuple[int, TType], ThriftField]

        def __init__(self, **kwargs) -> None: ...
else:
    ThriftObject = 'ThriftObject'

TYPE_META = "net.maunium.thrift.type"
INDEX_META = "net.maunium.thrift.index"
SECONDARY_META = "net.maunium.thrift.secondary"

if sys.version_info >= (3, 7):
    def _get_type_class(typ):
        try:
            return typ.__origin__
        except AttributeError:
            return None
else:
    def _get_type_class(typ):
        try:
            return typ.__extra__
        except AttributeError:
            return None


def _guess_type(python_type, name: str) -> RecursiveType:
    if python_type == str or python_type == bytes:
        return RecursiveType(TType.BINARY, python_type=python_type)
    elif python_type == bool:
        return RecursiveType(TType.BOOL, python_type=python_type)
    elif python_type == int:
        raise ValueError(f"Ambiguous integer field {name}")
    elif python_type == float:
        return RecursiveType(TType.DOUBLE, python_type=python_type)
    elif attr.has(python_type):
        return RecursiveType(TType.STRUCT, python_type=python_type)

    type_class = _get_type_class(python_type)
    args = getattr(python_type, "__args__", None)
    if type_class == list:
        return RecursiveType(TType.LIST, item_type=_guess_type(args[0], f"{name} item"),
                             python_type=list)
    elif type_class == dict:
        return RecursiveType(TType.MAP, key_type=_guess_type(args[0], f"{name} key").type,
                             value_type=_guess_type(args[1], f"{name} value"), python_type=map)
    elif type_class == set:
        return RecursiveType(TType.SET, item_type=_guess_type(args[0], f"{name} item"),
                             python_type=set)

    raise ValueError(f"Unknown type {python_type} for {name}")


def autospec(clazz: Any) -> Type[ThriftObject]:
    """
    Automatically generate a thrift_spec dict based on attrs metadata.

    Args:
        clazz: The class to decorate.

    Returns:
        The class given as a parameter.
    """
    clazz.thrift_spec = {}
    clazz.thrift_spec_by_type = {}
    index = 0
    for field in attr.fields(clazz):
        try:
            field_type = field.metadata[TYPE_META]._replace(python_type=field.type)
        except KeyError:
            field_type = _guess_type(field.type, field.name)
        field_meta = ThriftField(field.name, field_type)
        if not field.metadata.get(SECONDARY_META, False):
            try:
                index = field.metadata[INDEX_META]
            except KeyError:
                index += 1
            clazz.thrift_spec[index] = field_meta
        clazz.thrift_spec_by_type[(index, field_type.type)] = field_meta
    return clazz


def field(thrift_type: Union[TType, RecursiveType] = None, index: Optional[int] = None,
          secondary: bool = False, **kwargs) -> attr.Attribute:
    """
    Specify an explicit type for the :meth:`autospec` decorator.

    Args:
        thrift_type: The thrift type to use for the field.
        subtype: The subtype, for multi-part types like lists and maps.
        index: Override value for the Thrift field index
        secondary: Mark as a secondary option when the first one at the same index
            doesn't have a value or is a different type.
        **kwargs: Other parameters to pass to :meth:`attr.ib`.

    Returns:
        The result of :meth:`attr.ib`
    """
    meta = kwargs.setdefault("metadata", {})
    if thrift_type is not None:
        meta[TYPE_META] = (RecursiveType(thrift_type) if isinstance(thrift_type, TType)
                           else thrift_type)
    if index is not None:
        meta[INDEX_META] = index
    if secondary:
        meta[SECONDARY_META] = True
    return attr.ib(**kwargs)
