# mautrix-facebook - A Matrix-Facebook Messenger puppeting bridge
# Copyright (C) 2019 Tulir Asokan
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
from typing import Iterator, Optional, TypeVar, Type
from abc import abstractmethod

from sqlalchemy import Table
from sqlalchemy.engine.base import Engine
from sqlalchemy.engine.result import RowProxy, ResultProxy
from sqlalchemy.sql.base import ImmutableColumnCollection
from sqlalchemy.sql.expression import Select, ClauseElement, and_
from sqlalchemy.ext.declarative import as_declarative


T = TypeVar('T', bound='Base')


@as_declarative()
class Base:
    """
    Base class for SQLAlchemy models. Provides SQLAlchemy declarative base features and some
    additional utilities.
    """

    db: Engine
    t: Table
    __table__: Table
    c: ImmutableColumnCollection

    @classmethod
    def _one_or_none(cls: Type[T], rows: ResultProxy) -> Optional[T]:
        """
        Try scanning one row from a ResultProxy and return ``None`` if it fails.

        Args:
            rows: The SQLAlchemy result to scan.

        Returns:
            The scanned object, or ``None`` if there were no rows.
        """
        try:
            return cls.scan(next(rows))
        except StopIteration:
            return None

    @classmethod
    def _all(cls: Type[T], rows: ResultProxy) -> Iterator[T]:
        """
        Scan all rows from a ResultProxy.

        Args:
            rows: The SQLAlchemy result to scan.

        Yields:
            Each row scanned with :meth:`scan`
        """
        for row in rows:
            yield cls.scan(row)

    @classmethod
    @abstractmethod
    def scan(cls: Type[T], row: RowProxy) -> T:
        """
        Read the data from a row into an object.

        Args:
            row: The RowProxy object.

        Returns:
            An object containing the information in the row.
        """

    @classmethod
    def _make_simple_select(cls: Type[T], *args: ClauseElement) -> Select:
        """
        Create a simple ``SELECT * FROM table WHERE <args>`` statement.

        Args:
            *args: The WHERE clauses. If there are many elements, they're joined with AND.

        Returns:
            The SQLAlchemy SELECT statement object.
        """
        if len(args) > 1:
            return cls.t.select().where(and_(*args))
        elif len(args) == 1:
            return cls.t.select().where(args[0])
        else:
            return cls.t.select()

    @classmethod
    def _select_all(cls: Type[T], *args: ClauseElement) -> Iterator[T]:
        """
        Select all rows with given conditions. This is intended to be used by table-specific
        select methods.

        Args:
            *args: The WHERE clauses. If there are many elements, they're joined with AND.

        Yields:
            The objects representing the rows read with :meth:`scan`
        """
        yield from cls._all(cls.db.execute(cls._make_simple_select(*args)))

    @classmethod
    def _select_one_or_none(cls: Type[T], *args: ClauseElement) -> T:
        """
        Select one row with given conditions. If no row is found, return ``None``. This is intended
        to be used by table-specific select methods.

        Args:
            *args: The WHERE clauses. If there are many elements, they're joined with AND.

        Returns:
            The object representing the matched row read with :meth:`scan`, or ``None`` if no rows
            matched.
        """
        return cls._one_or_none(cls.db.execute(cls._make_simple_select(*args)))

    @property
    @abstractmethod
    def _edit_identity(self: T) -> ClauseElement:
        """The SQLAlchemy WHERE clause used for editing and deleting individual rows.
        Usually AND of primary keys."""

    def edit(self: T, *, _update_values: bool = True, **values) -> None:
        """
        Edit this row.

        Args:
            _update_values: Whether or not the values in memory should be updated as well as the
                            values in the database.
            **values: The values to change.
        """
        with self.db.begin() as conn:
            conn.execute(self.t.update()
                         .where(self._edit_identity)
                         .values(**values))
        if _update_values:
            for key, value in values.items():
                setattr(self, key, value)

    def delete(self: T) -> None:
        """Delete this row."""
        with self.db.begin() as conn:
            conn.execute(self.t.delete().where(self._edit_identity))
