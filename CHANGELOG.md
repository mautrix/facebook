# v0.5.0 (2023-05-31)

* Added config option to disable bridging `m.notice` messages
  (thanks to [@jzapataikono] in [#280]).
* Added options to automatically ratchet/delete megolm sessions to minimize
  access to old messages.
* Added option to not set room name/avatar even in encrypted rooms.
* Added option to disable reply fallbacks entirely.
* Added notice message when a call is received on Messenger.
* Redid backfill system to support MSC2716.
  * Note that using Synapse's MSC2716 implementation is not recommended, and
    the bridge can still backfill messages without MSC2716.
* Implemented appservice pinging using MSC2659.
* Possibly improved MQTT connection handling.
* Fixed bridging profile pictures for Instagram users.
* Fixed MQTT connection failing in certain cases when Facebook returned weird
  data in the chat list.

[@jzapataikono]: https://github.com/jzapataikono
[#280]: https://github.com/mautrix/facebook/pull/280

# v0.4.1 (2022-11-15)

* Improved unsupported message fallbacks and added support for more message
  types from Facebook.
  * Powerup messages, descriptions in story attachments, facebook pay messages
    and group join links among other things should be bridged now.
* Added support for dynamically fetching a proxy URL.
* Added option to bypass startup script in docker image.

# v0.4.0 (2022-04-06)

* Dropped Python 3.7 support.
* Added optional support for bridging presence from Facebook to Matrix
  (thanks to [@JakuJ] in [#189]).
* Added option to not resync chats on startup and instead ask the server to
  just send missed messages.
* Added option to use [MSC2246] async media uploads.
* Changed some fields to stop the user from showing up as online on Facebook
  all the time.
* Changed incoming message handling to preserve order from Facebook to Matrix.
* Fixed calculating mention offsets (mentioning users in messages with
  complicated unicode characters like emojis).
  * This will break message rendering that involves mentions and emojis in the
    Messenger web app, but it works everywhere else. The issue on web happens
    even with messages sent from the official apps.
* Fixed bridging reactions from Facebook in certain cases.
* Fixed bridging files from Matrix with non-ascii names.

[@JakuJ]: https://github.com/JakuJ
[#189]: https://github.com/mautrix/facebook/pull/189
[MSC2246]: https://github.com/matrix-org/matrix-spec-proposals/pull/2246

# v0.3.3 (2022-01-29)

* Added relay mode.
* Added automatic conversion of voice messages in both directions (mp4/aac to facebook and ogg/opus to Matrix).
* Added external URLs to unsupported attachment messages and story reply messages.
* Added support for typing notifications in both directions.
* Added Python 3.10 support.
* Removed legacy community features.
* Changed example config to disable temporary disconnect notices by default.
* Updated Docker image to Alpine 3.15.
* Formatted all code using [black](https://github.com/psf/black) and [isort](https://github.com/PyCQA/isort). 

# v0.3.2 (2021-11-14)

* (Re-)Added support for using SQLite as the bridge database.
* Added option to not use `http_proxy` env var for downloading files from Facebook CDN.
* Changed MQTT error handling to always refresh connection instead of giving up
  if it errors more than once within 2 minutes.
* Fixed `login-matrix` not allowing login from other servers.
* Fixed setting portal avatars.
* Fixed error when receiving a reply to an unknown message.

# v0.3.1 (2021-08-07)

**N.B.** Docker images have moved from `dock.mau.dev/tulir/mautrix-facebook` to
`dock.mau.dev/mautrix/facebook`. New versions are only available at the new path.

* Re-added `http_proxy` support for the Facebook connection.
* Updated Docker image to Alpine 3.14.
* Fixed messages being dropped if they came in while the portal was being created.
* Fixed bridge info causing canonical JSON errors due to the `id` field not
  being stringified.

# v0.3.0 (2021-05-02)

### Removed
* Removed Alembic. Database schema upgrades are handled automatically at
  startup as of v0.2.0. If upgrading from an older version, upgrade to v0.2.0
  first using the [upgrade instructions](https://docs.mau.fi/bridges/python/facebook/upgrading-to-v0.2.0.html).

### Added
* Support for per-room displaynames (#11).
* Syncing of read receipts after backfilling.
* Option for syncing notification settings from Facebook.

### Improved
* `fbrpc:` are now handled properly instead of being posted as-is to the Matrix room.
* All HTTP requests are now retried by default if the homeserver isn't reachable.

### Fixed
* Fixed some edge cases where replies and other message references wouldn't work because the bridge hadn't received the message ID.
* Fixed bridging audio messages and other things containing 32-bit floats.
* Fixed handling multiple mentions in a Messenger message (#144).
* Fixed periodic reconnect failing if user was disconnected (thanks to @mrjohnson22 in #143).

# v0.2.1 (2021-02-28)

* Added web-based login interface to prevent the bridge and homeserver from seeing passwords.
* Fixed error if bridge bot doesn't have permission to redact password when logging in.

# v0.2.0 (2021-02-24)

Breaking change: switched to Messenger mobile app API. Old cookie logins will
no longer work, all users have to relogin. See upgrade instructions
[on docs.mau.fi](https://docs.mau.fi/bridges/python/facebook/upgrading-to-v0.2.0.html).

# v0.1.2 (2020-12-11)

No changelog

# v0.1.1 (2020-11-11)

No changelog

# v0.1.0 (2020-10-05)

Initial release

## rc3 (2020-07-10)

## rc2 (2020-07-08)

## rc1 (2020-07-03)
