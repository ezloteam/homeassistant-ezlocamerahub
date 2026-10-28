"""Immutable models of the engine's control-plane JSON.

Parsing is strict about shape (a wrong type at the top level means we are
not talking to ha-camerasd) and lenient about optional fields, which older
engine builds omit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .const import MODE_CONTINUOUS
from .errors import InvalidResponseError

_EMPTY: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class CameraState:
    """Live per-camera state from /status."""

    mode: str
    recording: bool
    rtsp_ok: bool
    transport: str | None
    motion_capable: bool
    codec: str | None
    last_rtp_ts: int
    # None when the engine predates motion_active / last_motion_ts.
    motion_active: bool | None
    last_motion_ts: int | None


@dataclass(frozen=True, slots=True)
class CameraUploads:
    """Per-camera upload counters from /status."""

    # clips_ok counts uploaded HLS segments (~4 s each), not clips.
    clips_ok: int
    errors: int
    last_upload_ts: int
    # One per finalized recording; None when the engine predates them.
    clips_done: int | None
    clips_failed: int | None
    last_clip_ts: int | None


@dataclass(frozen=True, slots=True)
class HubStatus:
    """The engine's /status."""

    provisioned: bool
    last_error: str | None
    serial: str
    uuid: str
    mac_address: str
    signed_in: bool
    nma_connected: bool
    motion_supported: bool
    uploads_done: int
    uploads_pending: int
    uploads_failed: int
    cameras: Mapping[str, CameraState]
    per_camera_uploads: Mapping[str, CameraUploads]


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """A camera as configured on the engine (/cameras)."""

    id: str
    name: str
    rtsp_url: str = field(repr=False)
    subcategory: str | None
    mode: str
    transport: str | None


@dataclass(frozen=True, slots=True)
class Upload:
    """One row of the /uploads ring."""

    id: int
    ts: int
    camera_id: str
    source: str | None
    state: str | None
    bytes: int
    error: str | None


@dataclass(frozen=True, slots=True)
class Camera:
    """A camera's configuration merged with its live state."""

    id: str
    name: str
    rtsp_url: str = field(repr=False)
    mode: str
    transport: str | None
    recording: bool
    rtsp_ok: bool
    motion_capable: bool
    codec: str | None
    clips_ok: int
    upload_errors: int
    last_upload_ts: int | None
    last_clip_source: str | None
    clips_done: int | None
    clips_failed: int | None
    last_clip_ts: int | None
    motion_active: bool | None
    last_motion_ts: int | None

    @property
    def motion_supported(self) -> bool:
        """Whether the engine reports this camera's real-time motion state."""
        return self.motion_active is not None

    @property
    def clip_counters_supported(self) -> bool:
        """Whether the engine counts finalized recordings for this camera."""
        return self.clips_done is not None

    @property
    def recording_now(self) -> bool:
        """Whether the hub is writing this camera's video right now.

        The engine's `recording` means "recorder armed": in motion mode it is
        true while idle, and in continuous mode it stays true while RTSP
        ingest is down. Only continuous mode with live ingest is known to be
        recording.
        """
        return self.mode == MODE_CONTINUOUS and self.recording and self.rtsp_ok


@dataclass(frozen=True, slots=True)
class HubData:
    """Everything one coordinator refresh learned."""

    status: HubStatus
    cameras: Mapping[str, Camera]
    uploads: tuple[Upload, ...]


def _obj(value: Any, what: str) -> Mapping[str, Any]:
    if value is None:
        return _EMPTY
    if not isinstance(value, Mapping):
        raise InvalidResponseError(f"{what} is not an object")
    return value


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_camera_state(raw: Mapping[str, Any]) -> CameraState:
    return CameraState(
        mode=_str(raw.get("mode")) or MODE_CONTINUOUS,
        recording=raw.get("recording") is True,
        rtsp_ok=raw.get("rtsp_ok") is True,
        transport=_str(raw.get("transport")),
        motion_capable=raw.get("motion_capable") is True,
        codec=_str(raw.get("codec")),
        last_rtp_ts=_int(raw.get("last_rtp_ts")),
        motion_active=raw["motion_active"]
        if isinstance(raw.get("motion_active"), bool)
        else None,
        last_motion_ts=_int(raw.get("last_motion_ts")) or None,
    )


def _parse_camera_uploads(raw: Mapping[str, Any]) -> CameraUploads:
    return CameraUploads(
        clips_ok=_int(raw.get("clips_ok")),
        errors=_int(raw.get("errors")),
        last_upload_ts=_int(raw.get("last_upload_ts")),
        clips_done=_opt_int(raw.get("clips_done")),
        clips_failed=_opt_int(raw.get("clips_failed")),
        last_clip_ts=_opt_int(raw.get("last_clip_ts")),
    )


def parse_status(raw: Any) -> HubStatus:
    """Parse GET /app/standalone/status."""
    if not isinstance(raw, Mapping):
        raise InvalidResponseError("status is not an object")
    hub = _obj(raw.get("hub"), "hub")
    account = _obj(raw.get("account"), "account")
    nma = _obj(raw.get("nma"), "nma")
    capabilities = _obj(raw.get("capabilities"), "capabilities")
    uploads = _obj(raw.get("uploads"), "uploads")
    cameras = _obj(raw.get("cameras"), "cameras")
    per_camera = _obj(uploads.get("per_camera"), "uploads.per_camera")
    serial = hub.get("serial")
    return HubStatus(
        provisioned=raw.get("provisioned") is True,
        last_error=_str(raw.get("last_error")),
        # hub.serial is serialized as a string; accept a number defensively.
        serial=str(serial) if isinstance(serial, str | int) and serial != "" else "",
        uuid=_str(hub.get("uuid")) or "",
        mac_address=_str(hub.get("mac_address")) or "",
        signed_in=account.get("signed_in") is True,
        nma_connected=nma.get("connected") is True,
        motion_supported=capabilities.get("motion") is True,
        uploads_done=_int(uploads.get("done")),
        uploads_pending=_int(uploads.get("pending")),
        uploads_failed=_int(uploads.get("failed")),
        cameras=MappingProxyType(
            {
                cam_id: _parse_camera_state(value)
                for cam_id, value in cameras.items()
                if isinstance(value, Mapping)
            }
        ),
        per_camera_uploads=MappingProxyType(
            {
                cam_id: _parse_camera_uploads(value)
                for cam_id, value in per_camera.items()
                if isinstance(value, Mapping)
            }
        ),
    )


def parse_cameras(raw: Any) -> tuple[CameraConfig, ...]:
    """Parse GET /app/standalone/cameras."""
    items = _obj(raw, "cameras response").get("cameras") if raw is not None else None
    if not isinstance(items, list):
        raise InvalidResponseError("cameras is not a list")
    result = []
    for item in items:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("id"), str)
            or not item["id"]
        ):
            raise InvalidResponseError("camera without a string id")
        result.append(
            CameraConfig(
                id=item["id"],
                name=_str(item.get("name")) or item["id"],
                rtsp_url=_str(item.get("rtsp_url")) or "",
                subcategory=_str(item.get("subcategory")),
                mode=_str(item.get("mode")) or MODE_CONTINUOUS,
                transport=_str(item.get("transport")),
            )
        )
    return tuple(result)


def parse_uploads(raw: Any) -> tuple[Upload, ...]:
    """Parse GET /app/standalone/uploads, newest first; malformed rows are skipped."""
    items = _obj(raw, "uploads response").get("items", [])
    if not isinstance(items, list):
        raise InvalidResponseError("uploads items is not a list")
    return tuple(
        Upload(
            id=item["id"],
            ts=_int(item.get("ts")),
            camera_id=item["camera_id"],
            source=_str(item.get("source")),
            state=_str(item.get("state")),
            bytes=_int(item.get("bytes")),
            error=_str(item.get("error")),
        )
        for item in items
        if isinstance(item, Mapping)
        and isinstance(item.get("id"), int)
        and isinstance(item.get("camera_id"), str)
    )


def _last_clip_source(uploads: Iterable[Upload], camera_id: str) -> str | None:
    return next((u.source for u in uploads if u.camera_id == camera_id), None)


def build_hub_data(
    status: HubStatus, cameras: Iterable[CameraConfig], uploads: tuple[Upload, ...]
) -> HubData:
    """Merge /cameras with the live state from /status and /uploads."""
    merged: dict[str, Camera] = {}
    for config in cameras:
        live = status.cameras.get(config.id)
        counters = status.per_camera_uploads.get(config.id)
        merged[config.id] = Camera(
            id=config.id,
            name=config.name,
            rtsp_url=config.rtsp_url,
            mode=live.mode if live else config.mode,
            transport=(live.transport if live else None) or config.transport,
            recording=live.recording if live else False,
            rtsp_ok=live.rtsp_ok if live else False,
            motion_capable=live.motion_capable if live else False,
            codec=live.codec if live else None,
            clips_ok=counters.clips_ok if counters else 0,
            upload_errors=counters.errors if counters else 0,
            last_upload_ts=(counters.last_upload_ts or None) if counters else None,
            last_clip_source=_last_clip_source(uploads, config.id),
            clips_done=counters.clips_done if counters else None,
            clips_failed=counters.clips_failed if counters else None,
            last_clip_ts=(counters.last_clip_ts or None) if counters else None,
            motion_active=live.motion_active if live else None,
            last_motion_ts=live.last_motion_ts if live else None,
        )
    return HubData(status=status, cameras=MappingProxyType(merged), uploads=uploads)
