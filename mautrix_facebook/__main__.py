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
import argparse
import asyncio
import logging
import logging.config
import signal
import copy
import sys

import sqlalchemy as sql

from mautrix.appservice import AppService

from .config import Config
from .db import Base, init as init_db
from .sqlstatestore import SQLStateStore
from .user import User, init as init_user
from .portal import init as init_portal
from .puppet import Puppet, init as init_puppet
from .matrix import MatrixHandler
from .context import Context
from . import __version__

parser = argparse.ArgumentParser(
    description="A Matrix-Facebook Messenger puppeting bridge.",
    prog="python -m mautrix-facebook")
parser.add_argument("-c", "--config", type=str, default="config.yaml",
                    metavar="<path>", help="the path to your config file")
parser.add_argument("-b", "--base-config", type=str, default="example-config.yaml",
                    metavar="<path>", help="the path to the example config "
                                           "(for automatic config updates)")
parser.add_argument("-g", "--generate-registration", action="store_true",
                    help="generate registration and quit")
parser.add_argument("-r", "--registration", type=str, default="registration.yaml",
                    metavar="<path>", help="the path to save the generated registration to")
args = parser.parse_args()

config = Config(args.config, args.registration, args.base_config)
config.load()
config.update()

if args.generate_registration:
    config.generate_registration()
    config.save()
    print(f"Registration generated and saved to {config.registration_path}")
    sys.exit(0)

logging.config.dictConfig(copy.deepcopy(config["logging"]))
log = logging.getLogger("mau.init")  # type: logging.Logger
log.debug(f"Initializing mautrix-facebook {__version__}")

db_engine = sql.create_engine(config["appservice.database"] or "sqlite:///mautrix-facebook.db")
Base.metadata.bind = db_engine
init_db(db_engine)

loop = asyncio.get_event_loop()

state_store = SQLStateStore()
mebibyte = 1024 ** 2
appserv = AppService(config["homeserver.address"], config["homeserver.domain"],
                     config["appservice.as_token"], config["appservice.hs_token"],
                     config["appservice.bot_username"], log="mau.as", loop=loop,
                     verify_ssl=config["homeserver.verify_ssl"], state_store=state_store,
                     real_user_content_key="net.maunium.facebook.puppet",
                     aiohttp_params={
                         "client_max_size": config["appservice.max_body_size"] * mebibyte
                     })

context = Context(az=appserv, config=config, loop=loop)
context.mx = MatrixHandler(context)

init_user(context)
init_portal(context)
init_puppet(context)

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.default_int_handler)


async def start():
    log.debug("Starting web server")
    await appserv.start(config["appservice.hostname"], config["appservice.port"])
    log.debug("Initializing appservice bot")
    await context.mx.init_as_bot()
    log.debug("Loading custom puppets")
    await asyncio.gather(*[puppet.init_custom_mxid()
                           for puppet in Puppet.get_all_with_custom_mxid()])
    log.debug("Loading sessions")
    await asyncio.gather(*[user.load_session() for user in User.get_all()], loop=loop)


async def stop():
    log.debug("Stopping web server")
    await appserv.stop()

    for mxid, user in User.by_mxid.items():
        user.save()


try:
    log.debug("Running startup actions...")
    loop.run_until_complete(start())
    log.debug("Startup actions complete, running forever")
    loop.run_forever()
except KeyboardInterrupt:
    log.debug("Interrupt received")
    loop.run_until_complete(stop())
    sys.exit(0)
except Exception:
    log.exception("Unexpected error")
    sys.exit(1)
