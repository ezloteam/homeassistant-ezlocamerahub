"""Constants for the Ezlo Camera Hub integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ezlo_camerahub"
MANUFACTURER: Final = "Ezlo"

ENGINE_HOST: Final = "127.0.0.1"

# Config entry data: the Ezlo login. The password is kept only until the
# engine has provisioned.
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"

# Camera subentries.
SUBENTRY_CAMERA: Final = "camera"
CONF_NAME: Final = "name"
CONF_STREAM_URL: Final = "stream_url"
CONF_TRANSPORT: Final = "transport"
CONF_MODE: Final = "mode"
CONF_CAMERA_ID: Final = "camera_id"
# /status is cheap (no network on the engine side) and motion windows are
# short (5 s post-roll), so poll often enough to catch them.
SCAN_INTERVAL: Final = timedelta(seconds=5)
REQUEST_TIMEOUT: Final = 10

MODE_CONTINUOUS: Final = "continuous"
MODE_MOTION: Final = "motion"
MODES: Final = (MODE_CONTINUOUS, MODE_MOTION)

TRANSPORT_AUTO: Final = "auto"
TRANSPORTS: Final = (TRANSPORT_AUTO, "tcp", "udp")

STATUS_ARMED: Final = "armed"
STATUS_OFFLINE: Final = "offline"
STATUS_ONLINE: Final = "online"
STATUS_RECORDING: Final = "recording"

SOURCE_MOTION: Final = "motion"
SOURCE_CONTINUOUS: Final = "continuous"
