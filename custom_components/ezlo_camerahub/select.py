"""Select: a camera's recording mode (continuous / motion)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_MODE, DOMAIN, MODE_CONTINUOUS, MODE_MOTION
from .coordinator import CameraHubConfigEntry, CameraHubCoordinator
from .entity import CameraDeviceEntity, async_add_camera_entities
from .errors import CameraHubError

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CameraHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up mode selects when the engine supports motion recording at all."""
    coordinator = entry.runtime_data
    if not coordinator.data.status.motion_supported:
        # Pre-A5 engines only record continuously; there is nothing to pick.
        return
    async_add_camera_entities(
        coordinator,
        async_add_entities,
        lambda coord, subentry_id: [RecordingModeSelect(coord, subentry_id)],
    )


class RecordingModeSelect(CameraDeviceEntity, SelectEntity):
    """Switch a camera between continuous and motion-windowed recording."""

    _attr_translation_key = "recording_mode"

    def __init__(self, coordinator: CameraHubCoordinator, subentry_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, subentry_id, "recording_mode")

    @property
    def options(self) -> list[str]:
        """Motion is offered only for cameras with a working ONVIF motion source."""
        camera = self.camera
        if camera is not None and (camera.motion_capable or camera.mode == MODE_MOTION):
            return [MODE_CONTINUOUS, MODE_MOTION]
        return [MODE_CONTINUOUS]

    @property
    def current_option(self) -> str | None:
        """Return the camera's mode."""
        camera = self.camera
        return camera.mode if camera and camera.mode in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Set the mode on the engine; it keeps the camera id and restarts the recorder."""
        camera = self.camera
        camera_id = self.camera_id
        name = self._device_name
        if option == MODE_MOTION and not (camera and camera.motion_capable):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="motion_not_supported",
                translation_placeholders={"camera": name},
            )
        try:
            if camera_id is None:
                raise CameraHubError("the engine doesn't have this camera")
            await self.coordinator.client.async_set_mode(camera_id, option)
        except CameraHubError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_mode_failed",
                translation_placeholders={"camera": name, "error": str(err)},
            ) from err
        entry = self.coordinator.config_entry
        if (sub := entry.subentries.get(self.subentry_id)) is not None:
            # Keep the subentry's mode current: it's what the camera gets if
            # the engine ever has to add it again.
            self.hass.config_entries.async_update_subentry(
                entry, sub, data={**sub.data, CONF_MODE: option}
            )
        # A user action: show the new mode now rather than after the debounce.
        await self.coordinator.async_refresh()
