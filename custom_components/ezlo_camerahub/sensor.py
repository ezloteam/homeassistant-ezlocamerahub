"""Sensors: per-camera mode, status and uploads; hub-wide upload counters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    MODE_MOTION,
    MODES,
    SOURCE_CONTINUOUS,
    SOURCE_MOTION,
    STATUS_ARMED,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_RECORDING,
)
from .coordinator import CameraHubConfigEntry, CameraHubCoordinator
from .entity import CameraDeviceEntity, HubEntity, async_add_camera_entities
from .models import Camera, HubStatus

PARALLEL_UPDATES = 0

type SensorValue = str | int | datetime | None


def _camera_status(camera: Camera) -> str:
    if not camera.rtsp_ok:
        # In motion mode the engine opens the stream only on motion; an
        # answering ONVIF event service shows the camera is reachable.
        if camera.mode == MODE_MOTION and camera.motion_capable:
            return STATUS_ARMED
        return STATUS_OFFLINE
    return STATUS_RECORDING if camera.recording_now else STATUS_ONLINE


def _timestamp(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, UTC) if ts else None


@dataclass(frozen=True, kw_only=True)
class CameraSensorDescription(SensorEntityDescription):
    """Describes a per-camera sensor."""

    value_fn: Callable[[Camera], SensorValue]


@dataclass(frozen=True, kw_only=True)
class HubSensorDescription(SensorEntityDescription):
    """Describes a hub-wide sensor."""

    value_fn: Callable[[HubStatus], SensorValue]


CAMERA_SENSORS: tuple[CameraSensorDescription, ...] = (
    CameraSensorDescription(
        key="recording_mode",
        translation_key="recording_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(MODES),
        value_fn=lambda camera: camera.mode if camera.mode in MODES else None,
    ),
    CameraSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[STATUS_ARMED, STATUS_OFFLINE, STATUS_ONLINE, STATUS_RECORDING],
        value_fn=_camera_status,
    ),
    CameraSensorDescription(
        key="clips_uploaded",
        translation_key="clips_uploaded",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda camera: camera.clips_ok,
    ),
    CameraSensorDescription(
        key="upload_errors",
        translation_key="upload_errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda camera: camera.upload_errors,
    ),
    CameraSensorDescription(
        key="last_upload",
        translation_key="last_upload",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda camera: _timestamp(camera.last_upload_ts),
    ),
    CameraSensorDescription(
        key="last_clip_source",
        translation_key="last_clip_source",
        device_class=SensorDeviceClass.ENUM,
        options=[SOURCE_CONTINUOUS, SOURCE_MOTION],
        # /uploads is a bounded global ring (ha-camerasd #33): a busy
        # continuous camera can evict motion clips, so this may be unknown.
        value_fn=lambda camera: (
            camera.last_clip_source
            if camera.last_clip_source in (SOURCE_CONTINUOUS, SOURCE_MOTION)
            else None
        ),
    ),
)

# Only for cameras whose engine reports motion state (motion_active present).
MOTION_SENSORS: tuple[CameraSensorDescription, ...] = (
    CameraSensorDescription(
        key="last_motion",
        translation_key="last_motion",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda camera: _timestamp(camera.last_motion_ts),
    ),
)

HUB_SENSORS: tuple[HubSensorDescription, ...] = (
    HubSensorDescription(
        key="uploads_done",
        translation_key="uploads_done",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.uploads_done,
    ),
    HubSensorDescription(
        key="uploads_pending",
        translation_key="uploads_pending",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.uploads_pending,
    ),
    HubSensorDescription(
        key="uploads_failed",
        translation_key="uploads_failed",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.uploads_failed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CameraHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        HubSensor(coordinator, description) for description in HUB_SENSORS
    )
    async_add_camera_entities(
        coordinator,
        async_add_entities,
        lambda coord, subentry_id: [
            CameraSensor(coord, subentry_id, description)
            for description in CAMERA_SENSORS
        ],
    )
    async_add_camera_entities(
        coordinator,
        async_add_entities,
        lambda coord, subentry_id: [
            CameraSensor(coord, subentry_id, description)
            for description in MOTION_SENSORS
        ],
        include=lambda camera: camera.motion_supported,
    )


class CameraSensor(CameraDeviceEntity, SensorEntity):
    """A per-camera sensor."""

    entity_description: CameraSensorDescription

    def __init__(
        self,
        coordinator: CameraHubCoordinator,
        subentry_id: str,
        description: CameraSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, subentry_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> SensorValue:
        """Return the value."""
        camera = self.camera
        return self.entity_description.value_fn(camera) if camera else None


class HubSensor(HubEntity, SensorEntity):
    """A hub-wide sensor."""

    entity_description: HubSensorDescription

    def __init__(
        self, coordinator: CameraHubCoordinator, description: HubSensorDescription
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> SensorValue:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator.data.status)
