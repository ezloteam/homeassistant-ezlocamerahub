"""Async client for the ha-camerasd :9997 control plane."""

from __future__ import annotations

from collections.abc import Callable
import ipaddress
import re
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, ContentTypeError

from .const import MODES, REQUEST_TIMEOUT, TRANSPORTS
from .errors import (
    CameraHubConnectionError,
    CameraHubResponseError,
    InvalidResponseError,
)
from .models import (
    CameraConfig,
    HubStatus,
    Upload,
    parse_cameras,
    parse_status,
    parse_uploads,
)

PREFIX = "/app/standalone"
_CAMERA_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _host_for_url(host: str) -> str:
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]"
    except ValueError:
        pass
    return host


class CameraHubClient:
    """Read and control one engine."""

    def __init__(
        self, session: ClientSession, host: str, port: int | Callable[[], int]
    ) -> None:
        """Initialize.

        port may be a callable: the supervised engine can move to another
        port when it restarts.
        """
        self._session = session
        self._host = _host_for_url(host)
        self._port = port

    @property
    def base_url(self) -> str:
        """The engine's base URL."""
        port = self._port() if callable(self._port) else self._port
        return f"http://{self._host}:{port}"

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        missing_ok: bool = False,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{PREFIX}{path}",
                json=json,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if missing_ok and resp.status == 404:
                    # cameras/uploads 404 until the hub is provisioned.
                    return None
                if resp.status >= 400:
                    raise CameraHubResponseError(resp.status)
                try:
                    return await resp.json(content_type=None)
                except (ContentTypeError, ValueError) as err:
                    raise InvalidResponseError("engine did not return JSON") from err
        except (ClientError, TimeoutError) as err:
            raise CameraHubConnectionError(str(err) or type(err).__name__) from err

    async def async_get_status(self) -> HubStatus:
        """GET /status."""
        return parse_status(await self._request("GET", "/status"))

    async def async_get_cameras(self) -> tuple[CameraConfig, ...]:
        """GET /cameras; empty while unprovisioned."""
        raw = await self._request("GET", "/cameras", missing_ok=True)
        return () if raw is None else parse_cameras(raw)

    async def async_get_uploads(self) -> tuple[Upload, ...]:
        """GET /uploads; empty while unprovisioned."""
        raw = await self._request("GET", "/uploads", missing_ok=True)
        return () if raw is None else parse_uploads(raw)

    async def async_add_camera(
        self, name: str, rtsp_url: str, transport: str, mode: str
    ) -> str:
        """POST /cameras; returns the new camera id."""
        if mode not in MODES:
            raise ValueError(f"invalid mode {mode!r}")
        if transport not in TRANSPORTS:
            raise ValueError(f"invalid transport {transport!r}")
        raw = await self._request(
            "POST",
            "/cameras",
            {"name": name, "rtsp_url": rtsp_url, "transport": transport, "mode": mode},
        )
        camera_id = raw.get("id") if isinstance(raw, dict) else None
        if not isinstance(camera_id, str) or not _CAMERA_ID.match(camera_id):
            raise InvalidResponseError("add camera returned no valid id")
        return camera_id

    async def async_delete_camera(self, camera_id: str) -> None:
        """DELETE /cameras/{id}; a camera that is already gone is fine."""
        if not _CAMERA_ID.match(camera_id):
            raise ValueError("invalid camera id")
        await self._request("DELETE", f"/cameras/{camera_id}", missing_ok=True)

    async def async_set_mode(self, camera_id: str, mode: str) -> None:
        """POST /cameras/{id}/mode; keeps the camera id and restarts its recorder."""
        if mode not in MODES:
            raise ValueError(f"invalid mode {mode!r}")
        if not _CAMERA_ID.match(camera_id):
            raise ValueError("invalid camera id")
        await self._request("POST", f"/cameras/{camera_id}/mode", {"mode": mode})
