from mautrix.util.async_db import UpgradeTable

upgrade_table = UpgradeTable()

from . import v01_initial_revision, v02_message_oti, v03_outbound_only
