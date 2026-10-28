"""The Ezlo Camera Hub integration: runs a Camera Hub engine inside Home Assistant."""

from __future__ import annotations

from pathlib import Path
import shutil

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import STORAGE_DIR

from .api import CameraHubClient
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN, ENGINE_HOST, MANUFACTURER
from .coordinator import CameraHubConfigEntry, CameraHubCoordinator
from .engine import EngineStartError, EngineSupervisor
from .engine_binary import (
    EngineBinary,
    EngineBinaryError,
    NoEnginePinned,
    UnsupportedPlatform,
)
from .entity import hub_identifier

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.SENSOR,
]


def storage_dir(hass: HomeAssistant) -> Path:
    """The integration's directory under Home Assistant's storage."""
    return Path(hass.config.path(STORAGE_DIR, DOMAIN))


def engine_cache_dir(hass: HomeAssistant, entry: ConfigEntry) -> Path:
    """Where the entry's engine keeps its identity and cameras."""
    return storage_dir(hass) / entry.entry_id / "engine-cache"


async def async_setup_entry(hass: HomeAssistant, entry: CameraHubConfigEntry) -> bool:
    """Start the engine and set up the hub."""

    def credentials() -> tuple[str, str] | None:
        if CONF_PASSWORD not in entry.data:
            return None
        return entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]

    supervisor = EngineSupervisor(
        hass,
        EngineBinary(hass, storage_dir(hass) / "bin"),
        engine_cache_dir(hass, entry),
        credentials,
    )
    try:
        await supervisor.async_start()
    except (NoEnginePinned, UnsupportedPlatform) as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="engine_unavailable",
            translation_placeholders={"error": str(err)},
        ) from err
    except (EngineBinaryError, EngineStartError, OSError) as err:
        raise ConfigEntryNotReady(f"Camera Hub engine did not start: {err}") from err

    client = CameraHubClient(
        async_get_clientsession(hass), ENGINE_HOST, lambda: supervisor.port
    )
    coordinator = CameraHubCoordinator(hass, entry, client, supervisor)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await supervisor.async_stop()
        raise
    entry.runtime_data = coordinator

    async def _stop_engine(_event: Event | None = None) -> None:
        await supervisor.async_stop()

    # Home Assistant doesn't unload config entries on shutdown.
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_engine)
    )

    status = coordinator.data.status
    hub_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={hub_identifier(coordinator)},
        connections=(
            {(dr.CONNECTION_NETWORK_MAC, status.mac_address)}
            if status.mac_address
            else set()
        ),
        manufacturer=MANUFACTURER,
        model="Camera Hub (Home Assistant)",
        name=entry.title,
        serial_number=status.serial or None,
    )
    coordinator.hub_device_id = hub_device.id

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: CameraHubConfigEntry
) -> None:
    """A camera subentry was added, changed or removed: sync the engine."""
    coordinator = entry.runtime_data
    if coordinator.data.status.provisioned:
        await coordinator.async_sync_cameras()
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: CameraHubConfigEntry) -> bool:
    """Unload a config entry and stop its engine."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.supervisor.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the engine's identity and cameras with the entry."""
    await hass.async_add_executor_job(
        shutil.rmtree, storage_dir(hass) / entry.entry_id, True
    )
