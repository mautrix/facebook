from mautrix.util.async_db import UpgradeTable

upgrade_table = UpgradeTable()

from . import (
    v01_initial_revision,
    v02_message_oti,
    v03_portal_meta_set,
    v04_relay_mode,
    v05_remove_communities,
    v06_store_user_seq_id,
    v07_store_reaction_timestamp,
    v08_backfill_queue,
    v09_portal_infinite_backfill,
    v10_user_thread_sync_status,
    v11_user_thread_sync_done_flag,
    v12_puppet_contact_info_set,
)
