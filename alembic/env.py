from alembic import context
from sqlalchemy import engine_from_config, pool
from logging.config import fileConfig

import sys
from os.path import abspath, dirname

sys.path.insert(0, dirname(dirname(abspath(__file__))))

from mautrix.util.db import Base
from mautrix_facebook.config import Config
import mautrix_facebook.db

config = context.config
mxfb_config_path = context.get_x_argument(as_dictionary=True).get("config", "config.yaml")
mxfb_config = Config(mxfb_config_path, None, None)
mxfb_config.load()
config.set_main_option("sqlalchemy.url", mxfb_config["appservice.database"].replace("%", "%%"))
fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
