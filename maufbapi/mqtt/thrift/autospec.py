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
from typing import Tuple, Union
import sys

import attr

from .type import TType

TYPE_META = "net.maunium.instagram.thrift.type"

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


Subtype = Union[None, TType, Tuple['Subtype', 'Subtype']]


def _guess_type(python_type, name: str) -> Tuple[TType, Subtype]:
    if python_type == str or python_type == bytes:
        return TType.BINARY, None
    elif python_type == bool:
        return TType.BOOL, None
    elif python_type == int:
        raise ValueError(f"Ambiguous integer field {name}")
    elif python_type == float:
        return TType.DOUBLE, None
    elif attr.has(python_type):
        return TType.STRUCT, None

    type_class = _get_type_class(python_type)
    args = getattr(python_type, "__args__", None)
    if type_class == list:
        return TType.LIST, _guess_type(args[0], f"{name} item")
    elif type_class == dict:
        return TType.MAP, (_guess_type(args[0], f"{name} key"),
                           _guess_type(args[1], f"{name} value"))
    elif type_class == set:
        return TType.SET, _guess_type(args[0], f"{name} item")

    raise ValueError(f"Unknown type {python_type} for {name}")


def autospec(clazz):
    """
    Automatically generate a thrift_spec dict based on attrs metadata.

    Args:
        clazz: The class to decorate.

    Returns:
        The class given as a parameter.
    """
    clazz.thrift_spec = {}
    index = 1
    for field in attr.fields(clazz):
        field_type, subtype = field.metadata.get(TYPE_META) or _guess_type(field.type, field.name)
        clazz.thrift_spec[index] = (field_type, field.name, subtype)
        index += 1
    return clazz


def field(thrift_type: TType, subtype: Subtype = None, **kwargs) -> attr.Attribute:
    """
    Specify an explicit type for the :meth:`autospec` decorator.

    Args:
        thrift_type: The thrift type to use for the field.
        subtype: The subtype, for multi-part types like lists and maps.
        **kwargs: Other parameters to pass to :meth:`attr.ib`.

    Returns:
        The result of :meth:`attr.ib`
    """
    kwargs.setdefault("metadata", {})[TYPE_META] = (thrift_type, subtype)
    return attr.ib(**kwargs)
