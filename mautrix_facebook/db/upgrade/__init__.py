from mautrix.util.async_db import UpgradeTable

upgrade_table = UpgradeTable()

from . import (
    v01_initial_revision,
    v02_message_oti,
    v03_portal_meta_set,
    v04_relay_mode,
    v05_remove_communities,
    v06_store_user_seq_id,
)
