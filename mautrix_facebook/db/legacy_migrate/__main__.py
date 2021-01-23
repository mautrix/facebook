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
from typing import Union
import argparse

from sqlalchemy import orm
import sqlalchemy as sql

parser = argparse.ArgumentParser(description="mautrix-telegram dbms migration script",
                                 prog="python -m mautrix_telegram.scripts.dbms_migrate")
parser.add_argument("-f", "--from-url", type=str, required=True, metavar="<url>",
                    help="the old database path")
parser.add_argument("-t", "--to-url", type=str, required=True, metavar="<url>",
                    help="the new database path")
parser.add_argument("-s", "--silent", action="store_true", help="No logs while migrating")
args = parser.parse_args()
silent = args.silent or False


def log(message, end="\n"):
    if not silent:
        print(message, end=end, flush=True)


def connect(to):
    from mautrix.util.db import Base
    from mautrix.client.state_store.sqlalchemy import RoomState, UserProfile
    from .tables import Portal, Message, Puppet, User, UserPortal, Contact, Reaction

    db_engine = sql.create_engine(to)
    db_factory = orm.sessionmaker(bind=db_engine)
    db_session: Union[orm.Session, orm.scoped_session] = orm.scoped_session(db_factory)
    Base.metadata.bind = db_engine

    return db_session, {
        "Portal": Portal,
        "Message": Message,
        "Puppet": Puppet,
        "User": User,
        "RoomState": RoomState,
        "UserProfile": UserProfile,
        "Reaction": Reaction,
    }


log("Connecting to old database")
session, tables = connect(args.from_url)

data = {}
for name, table in tables.items():
    log(f"Reading table {name}...", end=" ")
    data[name] = session.query(table).all()
    log("Done!")

log("Connecting to new database")
session, tables = connect(args.to_url)

for name, table in tables.items():
    log(f"Writing table {name}", end="")
    length = len(data[name])
    n = 0
    for row in data[name]:
        session.merge(row)
        n += 5
        if n >= length:
            log(".", end="")
            n = 0
    log(" Done!")

log("Committing changes to database...", end=" ")
session.commit()
log("Done!")
