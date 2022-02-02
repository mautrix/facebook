# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge.
# Copyright (C) 2022 Tulir Asokan
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
from __future__ import annotations

from typing import Any, Type, TypeVar
from enum import Enum
import io
import struct

from mautrix.types import ExtensibleEnum

from .type import RecursiveType, ThriftObject, TType

T = TypeVar("T", bound=ThriftObject)

_alpha_start = ord("a")
_alpha_length = ord("z") - ord("a") + 1


class ThriftReader(io.BytesIO):
    """
    ThriftReader implements decoding the Thrift Compact protocol into Python values.

    https://github.com/apache/thrift/blob/master/doc/specs/thrift-compact-protocol.md
    """

    _prev_field_id: int
    _stack: list[int]

    _prev_struct_id: int

    @property
    def _struct_id(self) -> str:
        """An incrementing alphabetical identifier used for pretty-printing structs."""
        self._prev_struct_id += 1
        return chr(_alpha_start + (self._prev_struct_id // _alpha_length)) + chr(
            _alpha_start + self._prev_struct_id % _alpha_length
        )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prev_field_id = 0
        self._prev_struct_id = -1
        self._stack = []

    def _push_stack(self) -> None:
        self._stack.append(self._prev_field_id)
        self._prev_field_id = 0

    def _pop_stack(self) -> None:
        if self._stack:
            self._prev_field_id = self._stack.pop()

    def _read_byte(self, signed: bool = False) -> int:
        return int.from_bytes(self.read(1), "big", signed=signed)

    def reset(self) -> None:
        """Reset the parser to the start of the data."""
        self.seek(0)
        self._prev_field_id = 0
        self._prev_struct_id = -1
        self._stack = []

    @staticmethod
    def _from_zigzag(val: int) -> int:
        return (val >> 1) ^ -(val & 1)

    def read_int(self) -> int:
        return self._from_zigzag(self.read_varint())

    def read_varint(self) -> int:
        shift = 0
        result = 0
        while True:
            byte = self._read_byte()
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result

    def read_field(self) -> tuple[TType, int]:
        byte = self._read_byte()
        type = TType(byte & 0x0F)
        if type == TType.STOP:
            return type, -1
        field_id_delta = byte >> 4
        self._prev_field_id = (
            self.read_int() if field_id_delta == 0 else self._prev_field_id + field_id_delta
        )
        return type, self._prev_field_id

    def read_val(self, type: TType) -> Any:
        """
        Read a primitive value.

        Args:
            type: The type of value to read.

        Returns:

        """
        if type == TType.TRUE:
            return True
        elif type == TType.FALSE:
            return False
        elif type == TType.BYTE:
            return self._read_byte()
        elif type == TType.BINARY:
            return self.read(self.read_varint())
        elif type in (TType.I16, TType.I32, TType.I64):
            # All sizes of ints are decoded the same way from zigzag.
            return self.read_int()
        elif type == TType.DOUBLE:
            # Doubles are encoded as little endian
            # https://github.com/apache/thrift/blob/master/doc/specs/thrift-compact-protocol.md#double-encoding
            return struct.unpack("<d", self.read(8))[0]
        elif type == TType.FLOAT:
            # This seems to be a facebook-specific 32-bit float
            return struct.unpack("<f", self.read(4))[0]
        else:
            raise ValueError(f"{type.name} is not a primitive type")

    def read_list_header(self) -> tuple[TType, int]:
        """
        Read the type and length metadata of a list or set.

        https://github.com/apache/thrift/blob/master/doc/specs/thrift-compact-protocol.md#list-and-set

        Returns:
            A tuple containing the item type and length of the list or set.
        """
        header_byte = self._read_byte()

        # The upstream Thrift spec uses different element type identifiers for list and map types,
        # but Facebook just uses the same types as structs.
        item_type = TType(header_byte & 0x0F)

        length = header_byte >> 4
        if length == 0x0F:
            length = self.read_varint()

        return item_type, length

    def read_map_header(self) -> tuple[TType, TType, int]:
        """
        Read the type and length metadata of a map.

        https://github.com/apache/thrift/blob/master/doc/specs/thrift-compact-protocol.md#map

        Returns:
            A tuple containing the key type, value type and length of the map.
        """
        pos = self.tell()
        if self._read_byte() == 0:
            # If the first byte is zero, the map is empty.
            return TType.STOP, TType.STOP, 0
        # Go back one byte so we can read the length varint normally.
        self.seek(pos)
        length = self.read_varint()

        types = self._read_byte()
        key_type = TType(types >> 4)
        value_type = TType(types & 0x0F)

        return key_type, value_type, length

    def skip(self, type: TType) -> None:
        """
        Skip the next field in the data. If the type is a struct, list, set or map, this will
        recursively skip everything it contains.

        Args:
            type: The type of the field.
        """
        if type == TType.STRUCT:
            self._push_stack()
            while True:
                field_type, _ = self.read_field()
                if field_type == TType.STOP:
                    break
                self.skip(field_type)
            self._pop_stack()
        elif type in (TType.LIST, TType.SET):
            item_type, length = self.read_list_header()
            for _ in range(length):
                self.skip(item_type)
        elif type == TType.MAP:
            key_type, value_type, length = self.read_map_header()
            for _ in range(length):
                self.skip(key_type)
                self.skip(value_type)
        else:
            self.read_val(type)

    def _read_kv(
        self, key_type: RecursiveType, value_type: RecursiveType, field_path: str, index: int
    ) -> tuple[Any, Any]:
        key = self.read_val_recursive(key_type, field_path=f"{field_path}[{index}::key]")
        value_path = repr(key) if isinstance(key, (str, bytes, int)) else f"{index}::value"
        value = self.read_val_recursive(value_type, field_path=f"{field_path}[{value_path}]")
        return key, value

    def read_val_recursive(self, rtype: RecursiveType, field_path: str = "root") -> Any:
        """
        Read any type of value from the buffer.

        Args:
            rtype: The exact type specification for the value to read.
            field_path: The recursive field path used for debugging.

        Returns:
            The parsed value.
        """
        if rtype.type == TType.STRUCT:
            self._push_stack()
            val = self.read_struct(rtype.python_type, field_path=field_path)
            self._pop_stack()
            return val
        elif rtype.type == TType.MAP:
            key_type, value_type, length = self.read_map_header()
            if length == 0:
                return {}
            elif key_type != rtype.key_type.type:
                raise ValueError(
                    f"Unexpected key type at {field_path}: "
                    f"expected {rtype.key_type.type.name}, got {key_type.name}"
                )
            elif value_type != rtype.value_type.type:
                raise ValueError(
                    f"Unexpected value type at {field_path}: "
                    f"expected {rtype.value_type.type.name}, got {value_type.name}"
                )
            return dict(
                self._read_kv(rtype.key_type, rtype.value_type, field_path, index)
                for index in range(length)
            )
        elif rtype.type in (TType.LIST, TType.SET):
            item_type, length = self.read_list_header()
            if item_type != rtype.item_type.type:
                raise ValueError(
                    f"Unexpected item type at {field_path}: "
                    f"expected {rtype.item_type.type.name}, got {item_type.name}"
                )
            data = (
                self.read_val_recursive(rtype.item_type, field_path=f"{field_path}[{i}]")
                for i in range(length)
            )
            return set(data) if rtype.type == TType.SET else list(data)
        else:
            if rtype.type == TType.BINARY and rtype.python_type != bytes:
                # For non-bytes python types, decode as UTF-8 and then call the
                # type constructor in case it's an enum or something like that.
                try:
                    return rtype.python_type(self.read_val(rtype.type).decode("utf-8"))
                except UnicodeDecodeError as e:
                    raise ValueError(f"Failed to decode string at {field_path}: {e}")
            elif isinstance(rtype.python_type, type) and issubclass(
                rtype.python_type, (ExtensibleEnum, Enum)
            ):
                return rtype.python_type(self.read_val(rtype.type))
            else:
                return self.read_val(rtype.type)

    def read_struct(self, type: Type[T], field_path: str = "root") -> T:
        """
        Assuming the data in the buffer is a Thrift struct, parse it into a dataclass.

        Args:
            type: The Python type to parse the struct into.
            field_path: The recursive field path used for debugging.

        Returns:
            An instance of the given type with the parsed data.
        """
        args = {}
        while True:
            field_type, field_index = self.read_field()
            if field_type == TType.STOP:
                break
            try:
                field_meta = type.thrift_spec[field_index]
            except KeyError:
                # If the field isn't present in the class at all, ignore it.
                self.skip(field_type)
                continue
            expected_type = TType.BOOL if field_type in (TType.TRUE, TType.FALSE) else field_type
            if field_meta.rtype.type != expected_type:
                raise ValueError(
                    f"Mismatching type for for field {field_meta.name}/#{field_index}"
                    f": expected {field_meta.rtype.type.name}, got {field_type.name}"
                )
            if expected_type == TType.BOOL:
                args[field_meta.name] = True if field_type == TType.TRUE else False
            else:
                fp = f"{field_path}.{field_meta.name}"
                args[field_meta.name] = self.read_val_recursive(field_meta.rtype, field_path=fp)
        try:
            return type(**args)
        except TypeError as e:
            raise ValueError(f"Failed to create {type.__name__} at {field_path}") from e

    def pretty_print(
        self, field_type: TType = TType.STRUCT, _indent: str = "", _prefix: str = ""
    ) -> None:
        """
        Pretty-print the value in the reader.
        Useful for debugging and reverse-engineering schemas.
        """
        if _prefix:
            print(f"{_indent}{_prefix} ", end="")
        if field_type in (TType.LIST, TType.SET):
            item_type, length = self.read_list_header()
            print(f"{item_type.name} {length} items")
            for i in range(length):
                self.pretty_print(item_type, _indent + "  ", f"{i + 1}.")
        elif field_type == TType.MAP:
            key_type, value_type, length = self.read_map_header()
            print(f"<{key_type.name}: {value_type.name}> - {length} items")
            for _ in range(length):
                key = self.read_val(key_type)
                self.pretty_print(value_type, _indent + "  ", f"{key}:")
        elif field_type == TType.STRUCT:
            struct_id = self._struct_id
            print(f"start-{struct_id}")
            self._push_stack()
            while True:
                subfield_type, subfield_index = self.read_field()
                if subfield_type == TType.STOP:
                    break
                self.pretty_print(
                    subfield_type, _indent + "  ", f"{subfield_index} ({subfield_type.name}):"
                )
            print(f"{_indent}end-{struct_id}")
            self._pop_stack()
        else:
            print(self.read_val(field_type))
