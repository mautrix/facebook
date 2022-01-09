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

from typing import Any, Collection
import io
import struct

from .type import ThriftObject, TType


class ThriftWriter(io.BytesIO):
    """
    ThriftWriter implements encoding Python values into the Thrift Compact protocol.

    https://github.com/apache/thrift/blob/master/doc/specs/thrift-compact-protocol.md
    """

    _prev_field_id: int
    _stack: list[int]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._prev_field_id = 0
        self._stack = []

    def _push_stack(self) -> None:
        self._stack.append(self._prev_field_id)
        self._prev_field_id = 0

    def _pop_stack(self) -> None:
        if self._stack:
            self._prev_field_id = self._stack.pop()

    def _write_byte(self, byte: int | TType) -> None:
        self.write(bytes([byte]))

    @staticmethod
    def _to_zigzag(val: int, bits: int) -> int:
        return (val << 1) ^ (val >> (bits - 1))

    def _write_varint(self, val: int) -> None:
        while True:
            byte = val & ~0x7F
            if byte == 0:
                self._write_byte(val)
                break
            elif byte == -128:
                self._write_byte(0)
                break
            else:
                self._write_byte((val & 0xFF) | 0x80)
                val = val >> 7

    def _write_word(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 16))

    def _write_int(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 32))

    def _write_long(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 64))

    def _write_field_begin(self, field_id: int, ttype: TType) -> None:
        ttype_val = ttype.value
        delta = field_id - self._prev_field_id
        if 0 < delta < 16:
            self._write_byte((delta << 4) | ttype_val)
        else:
            self._write_byte(ttype_val)
            self._write_word(field_id)
        self._prev_field_id = field_id

    def _write_string(self, val: str | bytes) -> None:
        if isinstance(val, str):
            val = val.encode("utf-8")
        self._write_varint(len(val))
        self.write(val)

    def write_map(self, field_id: int, key_type: TType, value_type: TType, val: dict) -> None:
        self._write_field_begin(field_id, TType.MAP)
        if not map:
            self._write_byte(0)
            return
        self._write_varint(len(val))
        self._write_byte(((key_type.value & 0xF) << 4) | (value_type.value & 0xF))
        for key, value in val.items():
            self.write_val(None, key_type, key)
            self.write_val(None, value_type, value)

    def write_stop(self) -> None:
        self._write_byte(TType.STOP.value)
        self._pop_stack()

    def write_list(self, field_id: int, item_type: TType, val: Collection[Any]) -> None:
        self._write_field_begin(field_id, TType.LIST)
        if len(val) < 0x0F:
            self._write_byte((len(val) << 4) | item_type.value)
        else:
            self._write_byte(0xF0 | item_type.value)
            self._write_varint(len(val))
        for item in val:
            self.write_val(None, item_type, item)

    def write_struct_begin(self, field_id: int) -> None:
        self._write_field_begin(field_id, TType.STRUCT)
        self._push_stack()

    def write_val(self, field_id: int | None, ttype: TType, val: Any) -> None:
        if ttype == TType.BOOL:
            if field_id is None:
                raise ValueError("booleans can only be used in structs")
            self._write_field_begin(field_id, TType.TRUE if val else TType.FALSE)
            return
        if field_id is not None:
            self._write_field_begin(field_id, ttype)
        if ttype == TType.BYTE:
            self._write_byte(val)
        elif ttype == TType.I16:
            self._write_word(val)
        elif ttype == TType.I32:
            self._write_int(val)
        elif ttype == TType.I64:
            self._write_long(val)
        elif ttype == TType.DOUBLE:
            self.write(struct.pack("<d", val))
        elif ttype == TType.BINARY:
            self._write_string(val)
        else:
            raise ValueError(f"{ttype} is not supported by write_val()")

    def write_struct(self, obj: ThriftObject) -> None:
        for field_id in iter(obj.thrift_spec):
            meta = obj.thrift_spec[field_id]

            val = getattr(obj, meta.name, None)
            if val is None:
                continue

            rtype = meta.rtype
            if rtype.type in (TType.LIST, TType.SET):
                self.write_list(field_id, rtype.item_type.type, val)
            elif rtype.type == TType.MAP:
                self.write_map(field_id, rtype.key_type.type, rtype.value_type.type, val)
            elif rtype.type == TType.STRUCT:
                self.write_struct_begin(field_id)
                self.write_struct(val)
            else:
                self.write_val(field_id, rtype.type, val)
        self.write_stop()
