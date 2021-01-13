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
from typing import List, Any, TypeVar, Type, Tuple
import struct
import io

from .type import TType
from .autospec import ThriftObject, RecursiveType

T = TypeVar('T', bound=ThriftObject)

alpha_start = ord("a")
alpha_length = ord("z") - ord("a") + 1


class ThriftReader(io.BytesIO):
    prev_field_id: int
    _struct_id: int
    stack: List[int]

    @property
    def struct_id(self) -> str:
        self._struct_id += 1
        return (chr(alpha_start + (self._struct_id // alpha_length))
                + chr(alpha_start + self._struct_id % alpha_length))

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prev_field_id = 0
        self._struct_id = -1
        self.stack = []

    def _push_stack(self) -> None:
        self.stack.append(self.prev_field_id)
        self.prev_field_id = 0

    def _pop_stack(self) -> None:
        if self.stack:
            self.prev_field_id = self.stack.pop()

    def _read_byte(self, signed: bool = False) -> int:
        return int.from_bytes(self.read(1), "big", signed=signed)

    def reset(self) -> None:
        self.seek(0)
        self.prev_field_id = 0
        self._struct_id = -1
        self.stack = []

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
            result |= (byte & 0x7f) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result

    def read_field(self) -> Tuple[TType, int]:
        byte = self._read_byte()
        if (byte & 0x0f) == 0:
            return TType.STOP, -1
        ttype = TType(byte & 0x0f)
        delta = byte >> 4
        if delta == 0:
            self.prev_field_id = self.read_int()
        else:
            self.prev_field_id += delta
        return ttype, self.prev_field_id

    def read_val(self, type: TType) -> Any:
        if type == TType.TRUE:
            return True
        elif type == TType.FALSE:
            return False
        elif type == TType.BYTE:
            return self._read_byte()
        elif type == TType.BINARY:
            return self.read(self.read_varint())
        elif type in (TType.I16, TType.I32, TType.I64):
            return self.read_int()
        elif type == TType.DOUBLE:
            return struct.unpack("f", self.read(8))

    def read_list_header(self) -> Tuple[TType, int]:
        item_type = self._read_byte()
        length = item_type >> 4
        item_type = TType(item_type & 0x0f)
        if length == 0x0f:
            length = self.read_varint()
        return item_type, length

    def read_map_header(self) -> Tuple[TType, TType, int]:
        pos = self.tell()
        if self._read_byte() == 0:
            return TType.STOP, TType.STOP, 0
        self.seek(pos)
        length = self.read_varint()
        types = self._read_byte()
        key_type = TType(types >> 4)
        value_type = TType(types & 0x0f)
        return key_type, value_type, length

    def skip(self, type: TType) -> None:
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

    def read_val_recursive(self, rtype: RecursiveType) -> Any:
        if rtype.type == TType.STRUCT:
            self._push_stack()
            val = self.read_struct(rtype.python_type)
            self._pop_stack()
            return val
        elif rtype.type == TType.MAP:
            key_type, value_type, length = self.read_map_header()
            if key_type != rtype.key_type:
                raise ValueError(f"Unexpected key type: expected {rtype.key_type}, got {key_type}")
            elif value_type != rtype.value_type.type:
                raise ValueError(f"Unexpected value type: expected {rtype.value_type.type}, "
                                 f"got {value_type}")
            return {self.read_val(key_type): self.read_val_recursive(rtype.value_type)
                    for _ in range(length)}
        elif rtype.type in (TType.LIST, TType.SET):
            item_type, length = self.read_list_header()
            if item_type != rtype.item_type.type:
                raise ValueError(f"Unexpected item type: expected {rtype.item_type.type}, "
                                 f"got {item_type}")
            data = (self.read_val_recursive(rtype.item_type) for _ in range(length))
            return set(data) if rtype.type == TType.SET else list(data)
        else:
            if rtype.type == TType.BINARY and rtype.python_type != bytes:
                return rtype.python_type(self.read_val(rtype.type).decode("utf-8"))
            return self.read_val(rtype.type)

    def read_struct(self, type: Type[T]) -> T:
        args = {}
        while True:
            field_type, field_index = self.read_field()
            if field_type == TType.STOP:
                break
            elif field_index not in type.thrift_spec:
                self.skip(field_type)
                continue
            try:
                field_meta = type.thrift_spec_by_type[(field_index, field_type)]
            except KeyError:
                raise ValueError("Couldn't find corresponding Python field "
                                 f"for Thrift field {field_index}/{field_type}")
            args[field_meta.name] = self.read_val_recursive(field_meta.rtype)
        # print("Creating a", type.__name__, "with", args)
        return type(**args)

    def pretty_print(self, field_type: TType = TType.STRUCT, _indent: str = "", _prefix: str = ""
                     ) -> None:
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
            struct_id = self.struct_id
            print(f"start-{struct_id}")
            self._push_stack()
            while True:
                subfield_type, subfield_index = self.read_field()
                if subfield_type == TType.STOP:
                    break
                self.pretty_print(subfield_type, _indent + "  ",
                                  f"{subfield_index} ({subfield_type.name}):")
            print(f"{_indent}end-{struct_id}")
            self._pop_stack()
        else:
            print(self.read_val(field_type))
