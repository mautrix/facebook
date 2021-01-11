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
from typing import Any, Union, List, Dict, Optional
import io

from .type import TType


class ThriftWriter(io.BytesIO):
    prev_field_id: int
    stack: List[int]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prev_field_id = 0
        self.stack = []

    def _push_stack(self) -> None:
        self.stack.append(self.prev_field_id)
        self.prev_field_id = 0

    def _pop_stack(self) -> None:
        if self.stack:
            self.prev_field_id = self.stack.pop()

    def _write_byte(self, byte: Union[int, TType]) -> None:
        self.write(bytes([byte]))

    @staticmethod
    def _to_zigzag(val: int, bits: int) -> int:
        return (val << 1) ^ (val >> (bits - 1))

    def _write_varint(self, val: int) -> None:
        while True:
            byte = val & ~0x7f
            if byte == 0:
                self._write_byte(val)
                break
            elif byte == -128:
                self._write_byte(0)
                break
            else:
                self._write_byte((val & 0xff) | 0x80)
                val = val >> 7

    def _write_word(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 16))

    def _write_int(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 32))

    def _write_long(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 64))

    def write_field_begin(self, field_id: int, ttype: TType) -> None:
        ttype_val = ttype.value
        delta = field_id - self.prev_field_id
        if 0 < delta < 16:
            self._write_byte((delta << 4) | ttype_val)
        else:
            self._write_byte(ttype_val)
            self._write_word(field_id)
        self.prev_field_id = field_id

    def write_map(self, field_id: int, key_type: TType, value_type: TType, val: Dict[Any, Any]
                  ) -> None:
        self.write_field_begin(field_id, TType.MAP)
        if not map:
            self._write_byte(0)
            return
        self._write_varint(len(val))
        self._write_byte(((key_type.value & 0xf) << 4) | (value_type.value & 0xf))
        for key, value in val.items():
            self.write_val(None, key_type, key)
            self.write_val(None, value_type, value)

    def write_string_direct(self, val: Union[str, bytes]) -> None:
        if isinstance(val, str):
            val = val.encode("utf-8")
        self._write_varint(len(val))
        self.write(val)

    def write_stop(self) -> None:
        self._write_byte(TType.STOP.value)
        self._pop_stack()

    def write_int8(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.BYTE)
        self._write_byte(val)

    def write_int16(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.I16)
        self._write_word(val)

    def write_int32(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.I32)
        self._write_int(val)

    def write_int64(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.I64)
        self._write_long(val)

    def write_list(self, field_id: int, item_type: TType, val: List[Any]) -> None:
        self.write_field_begin(field_id, TType.LIST)
        if len(val) < 0x0f:
            self._write_byte((len(val) << 4) | item_type.value)
        else:
            self._write_byte(0xf0 | item_type.value)
            self._write_varint(len(val))
        for item in val:
            self.write_val(None, item_type, item)

    def write_struct_begin(self, field_id: int) -> None:
        self.write_field_begin(field_id, TType.STRUCT)
        self._push_stack()

    def write_val(self, field_id: Optional[int], ttype: TType, val: Any) -> None:
        if ttype == TType.BOOL:
            if field_id is None:
                raise ValueError("booleans can only be in structs")
            self.write_field_begin(field_id, TType.TRUE if val else TType.FALSE)
            return
        if field_id is not None:
            self.write_field_begin(field_id, ttype)
        if ttype == TType.BYTE:
            self._write_byte(val)
        elif ttype == TType.I16:
            self._write_word(val)
        elif ttype == TType.I32:
            self._write_int(val)
        elif ttype == TType.I64:
            self._write_long(val)
        elif ttype == TType.BINARY:
            self.write_string_direct(val)
        else:
            raise ValueError(f"{ttype} is not supported by write_val()")

    def write_struct(self, obj: Any) -> None:
        for field_id in iter(obj.thrift_spec):
            field_type, field_name, inner_type = obj.thrift_spec[field_id]

            val = getattr(obj, field_name, None)
            if val is None:
                continue

            start = len(self.getvalue())
            if field_type in (TType.BOOL, TType.BYTE, TType.I16, TType.I32, TType.I64,
                              TType.BINARY):
                self.write_val(field_id, field_type, val)
            elif field_type in (TType.LIST, TType.SET):
                self.write_list(field_id, inner_type, val)
            elif field_type == TType.MAP:
                (key_type, _), (value_type, _) = inner_type
                self.write_map(field_id, key_type, value_type, val)
            elif field_type == TType.STRUCT:
                self.write_struct_begin(field_id)
                self.write_struct(val)
        self.write_stop()
