"""Polls the engine, keeps its cameras in line with the camera subentries."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CameraHubClient
from .const import (
    CONF_CAMERA_ID,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_STREAM_URL,
    CONF_TRANSPORT,
    CONF_USERNAME,
    DOMAIN,
    SCAN_INTERVAL,
    SUBENTRY_CAMERA,
)
from .errors import CameraHubError
from .models import HubData, HubStatus, build_hub_data
from .rtsp import compose_rtsp_url

if TYPE_CHECKING:
    from .engine import EngineSupervisor

_LOGGER = logging.getLogger(__name__)

type CameraHubConfigEntry = ConfigEntry[CameraHubCoordinator]


def camera_subentries(entry: ConfigEntry) -> Mapping[str, ConfigSubentry]:
    """The entry's camera subentries by subentry id."""
    return {
        sid: sub
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_CAMERA
    }


def subentry_rtsp_url(sub: ConfigSubentry) -> str:
    """The camera's RTSP URL with its credentials, as handed to the engine."""
    return compose_rtsp_url(
        sub.data[CONF_STREAM_URL],
        sub.data.get(CONF_USERNAME, ""),
        sub.data.get(CONF_PASSWORD, ""),
    )


def _rejected_login(status: HubStatus) -> bool:
    error = status.last_error or ""
    return "HTTP 401" in error or "HTTP 403" in error or "invalid_credentials" in error


class CameraHubCoordinator(DataUpdateCoordinator[HubData]):
    """Fetches /status, /cameras and /uploads together."""

    config_entry: CameraHubConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CameraHubConfigEntry,
        client: CameraHubClient,
        supervisor: EngineSupervisor,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.supervisor = supervisor
        # Registry id of the hub device, set once setup has registered it.
        self.hub_device_id: str | None = None
        self._synced = False
        self._sync_lock = asyncio.Lock()

    def camera_id_for(self, subentry_id: str) -> str | None:
        """The engine camera id behind a camera subentry."""
        sub = self.config_entry.subentries.get(subentry_id)
        return sub.data.get(CONF_CAMERA_ID) if sub else None

    async def _async_update_data(self) -> HubData:
        try:
            status = await self.client.async_get_status()
        except CameraHubError as err:
            raise UpdateFailed(
                f"Error talking to the Camera Hub engine: {err}"
            ) from err
        if not status.provisioned:
            self._check_provisioning(status)
        else:
            self._after_provisioned(status)
            if not self._synced:
                await self.async_sync_cameras()
        try:
            cameras, uploads = await asyncio.gather(
                self.client.async_get_cameras(), self.client.async_get_uploads()
            )
        except CameraHubError as err:
            raise UpdateFailed(
                f"Error talking to the Camera Hub engine: {err}"
            ) from err
        return build_hub_data(status, cameras, uploads)

    def _check_provisioning(self, status: HubStatus) -> None:
        if _rejected_login(status):
            raise ConfigEntryAuthFailed("The Ezlo cloud rejected the login")
        if CONF_PASSWORD not in self.config_entry.data:
            # The engine has no identity (first run, or its cache was lost)
            # and there is no password left to provision with.
            raise ConfigEntryAuthFailed(
                "The Camera Hub needs the Ezlo password to provision"
            )

    def _after_provisioned(self, status: HubStatus) -> None:
        entry = self.config_entry
        if CONF_PASSWORD in entry.data:
            # Provisioned: the engine boots from its cache from now on, so the
            # password is no longer kept.
            self.hass.config_entries.async_update_entry(
                entry, data={CONF_USERNAME: entry.data[CONF_USERNAME]}
            )
        if self.hub_device_id and status.serial:
            registry = dr.async_get(self.hass)
            device = registry.async_get(self.hub_device_id)
            if device is not None and device.serial_number != status.serial:
                registry.async_update_device(
                    self.hub_device_id, serial_number=status.serial
                )

    async def async_sync_cameras(self) -> None:
        """Make the engine's cameras match the camera subentries.

        Subentries are the source of truth: a subentry whose camera the
        engine lost is added again (it gets a new engine id), and an engine
        camera no subentry refers to is deleted. Errors leave it for the next
        refresh.
        """
        async with self._sync_lock:
            entry = self.config_entry
            try:
                engine_ids = {
                    camera.id for camera in await self.client.async_get_cameras()
                }
                wanted: set[str] = set()
                for sub in camera_subentries(entry).values():
                    camera_id = sub.data.get(CONF_CAMERA_ID)
                    if camera_id in engine_ids:
                        wanted.add(camera_id)
                        continue
                    new_id = await self.client.async_add_camera(
                        sub.title,
                        subentry_rtsp_url(sub),
                        sub.data[CONF_TRANSPORT],
                        sub.data[CONF_MODE],
                    )
                    wanted.add(new_id)
                    self.hass.config_entries.async_update_subentry(
                        entry,
                        sub,
                        data={**sub.data, CONF_CAMERA_ID: new_id},
                        unique_id=new_id,
                    )
                for camera_id in engine_ids - wanted:
                    await self.client.async_delete_camera(camera_id)
            except (CameraHubError, ValueError) as err:
                _LOGGER.warning(
                    "Could not sync cameras with the Camera Hub engine: %s", err
                )
                self._synced = False
                return
            self._synced = True
