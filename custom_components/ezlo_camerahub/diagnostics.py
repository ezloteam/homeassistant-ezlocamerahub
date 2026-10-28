"""Diagnostics, with camera credentials and hub identifiers removed."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CAMERA_ID,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_STREAM_URL,
    CONF_TRANSPORT,
)
from .coordinator import CameraHubConfigEntry, camera_subentries
from .rtsp import redact_rtsp_url

TO_REDACT = {"uuid", "mac_address"}


def _plain(value: Any) -> Any:
    """Convert models (frozen dataclasses, read-only mappings) to plain data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CameraHubConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    supervisor = coordinator.supervisor
    status = _plain(data.status)
    cameras = {
        cam_id: {
            **{k: v for k, v in _plain(camera).items() if k != "rtsp_url"},
            "stream": redact_rtsp_url(camera.rtsp_url),
        }
        for cam_id, camera in data.cameras.items()
    }
    return {
        "entry": {"password_stored": CONF_PASSWORD in entry.data},
        "engine": {
            "port": supervisor.port,
            "provisioned_cache": supervisor.provisioned,
            "failures": supervisor.failures,
            "last_error": supervisor.last_error,
        },
        "camera_subentries": {
            sid: {
                "stream": sub.data[CONF_STREAM_URL],
                "has_credentials": bool(sub.data.get(CONF_PASSWORD)),
                "transport": sub.data.get(CONF_TRANSPORT),
                "mode": sub.data.get(CONF_MODE),
                "camera_id": sub.data.get(CONF_CAMERA_ID),
            }
            for sid, sub in camera_subentries(entry).items()
        },
        "status": async_redact_data(status, TO_REDACT),
        "cameras": cameras,
        "recent_uploads": _plain(data.uploads[:20]),
    }
