"""Binary sensors: per-camera motion and the hub's cloud connection.

Motion comes from /status cameras.{id}.motion_active (the camera's own ONVIF
motion state right now). Engines that predate it omit the field, and then
no motion sensor is created: the older `recording` flag only means
"recorder armed" and is steadily true in motion mode.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CameraHubConfigEntry, CameraHubCoordinator
from .entity import CameraDeviceEntity, HubEntity, async_add_camera_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CameraHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities([CloudConnectionSensor(coordinator)])
    async_add_camera_entities(
        coordinator,
        async_add_entities,
        lambda coord, subentry_id: [MotionSensor(coord, subentry_id)],
        include=lambda camera: camera.motion_supported,
    )


class MotionSensor(CameraDeviceEntity, BinarySensorEntity):
    """On while the camera reports motion.

    The engine is polled, so a trip that starts and ends between two polls
    is caught from last_motion_ts advancing: the sensor is then on for one
    poll interval.
    """

    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: CameraHubCoordinator, subentry_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, subentry_id, "motion")
        camera = self.camera
        self._seen_motion_ts = camera.last_motion_ts if camera else None
        self._missed_motion = False

    @callback
    def _handle_coordinator_update(self) -> None:
        camera = self.camera
        motion_ts = camera.last_motion_ts if camera else None
        self._missed_motion = (
            motion_ts is not None and motion_ts != self._seen_motion_ts
        )
        if motion_ts is not None:
            self._seen_motion_ts = motion_ts
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Unavailable if the engine stops reporting motion state."""
        camera = self.camera
        return super().available and camera is not None and camera.motion_supported

    @property
    def is_on(self) -> bool | None:
        """Return whether motion is active, or happened since the last poll."""
        camera = self.camera
        if camera is None:
            return None
        return bool(camera.motion_active) or self._missed_motion


class CloudConnectionSensor(HubEntity, BinarySensorEntity):
    """Whether the hub is connected to the Ezlo cloud (NMA)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CameraHubCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "cloud_connection")

    @property
    def is_on(self) -> bool:
        """Return whether NMA is connected."""
        return self.coordinator.data.status.nma_connected
