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
from typing import List, Any
import struct
import io

from .type import TType


class ThriftReader(io.BytesIO):
    prev_field_id: int
    struct_id: int
    stack: List[int]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prev_field_id = 0
        self.struct_id = ord("a") - 1
        self.stack = []

    def _push_stack(self) -> None:
        self.stack.append(self.prev_field_id)
        self.prev_field_id = 0

    def _pop_stack(self) -> None:
        if self.stack:
            self.prev_field_id = self.stack.pop()

    def _read_byte(self, signed: bool = False) -> int:
        return int.from_bytes(self.read(1), "big", signed=signed)

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

    def read_field(self) -> TType:
        byte = self._read_byte()
        if byte == 0 or byte == 15:
            return TType.STOP
        delta = (byte & 0xf0) >> 4
        if delta == 0:
            self.prev_field_id = self.read_int()
        else:
            self.prev_field_id += delta
        return TType(byte & 0x0f)

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

    def pretty_print(self, field_type: TType, _indent: str = "", _prefix: str = "") -> None:
        if _prefix:
            print(f"{_indent}{_prefix} ", end="")
        if field_type in (TType.LIST, TType.SET):
            item_type = self._read_byte()
            length = item_type >> 4
            item_type = TType(item_type & 0x0f)
            if length == 0xf0:
                length = self.read_varint()
            print(f"{item_type.name} {length} items")
            for i in range(length):
                self.pretty_print(item_type, _indent + "  ", f"{i+1}.")
        elif field_type == TType.MAP:
            length = self.read_varint()
            types = self._read_byte()
            key_type = TType(types >> 4)
            value_type = TType(types & 0x0f)
            print(f"<{key_type.name}: {value_type.name}> - {length} items")
            for _ in range(length):
                key = self.read_val(key_type)
                self.pretty_print(value_type, _indent + "  ", f"{key}:")
        elif field_type == TType.STRUCT:
            self.struct_id += 1
            struct_id = chr(self.struct_id)
            print(f"start-{struct_id}")
            self._push_stack()
            while True:
                subfield_type = self.read_field()
                if subfield_type == TType.STOP:
                    break
                self.pretty_print(subfield_type, _indent + "  ",
                                  f"{self.prev_field_id} ({subfield_type.name}):")
            print(f"{_indent}end-{struct_id}")
            self._pop_stack()
        else:
            print(self.read_val(field_type))
