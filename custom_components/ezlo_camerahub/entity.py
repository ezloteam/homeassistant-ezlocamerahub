"""Base entities: one hub device, one device per camera subentry."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import CameraHubCoordinator, camera_subentries
from .models import Camera

# Home Assistant 2026.10 deprecates DeviceInfo.via_device (identifier) in
# favour of via_device_id (registry id); older releases only know via_device.
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def hub_identifier(coordinator: CameraHubCoordinator) -> tuple[str, str]:
    """The hub device's identifier."""
    return (DOMAIN, coordinator.config_entry.entry_id)


def _via_hub(coordinator: CameraHubCoordinator) -> DeviceInfo:
    if _HAS_VIA_DEVICE_ID and coordinator.hub_device_id:
        return DeviceInfo(via_device_id=coordinator.hub_device_id)  # type: ignore[typeddict-unknown-key]
    return DeviceInfo(via_device=hub_identifier(coordinator))


class HubEntity(CoordinatorEntity[CameraHubCoordinator]):
    """An entity on the hub device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CameraHubCoordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={hub_identifier(coordinator)})


class CameraDeviceEntity(CoordinatorEntity[CameraHubCoordinator]):
    """An entity on one camera's device.

    Keyed by the camera's subentry, which stays the same if the engine has
    to re-add the camera under a new id.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CameraHubCoordinator, subentry_id: str, key: str
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=coordinator.config_entry.subentries[subentry_id].title,
            manufacturer=MANUFACTURER,
            model="Camera Hub camera",
            **_via_hub(coordinator),
        )

    @property
    def _device_name(self) -> str:
        sub = self.coordinator.config_entry.subentries.get(self.subentry_id)
        return sub.title if sub else self.subentry_id

    @property
    def camera_id(self) -> str | None:
        """The engine's id for this camera."""
        return self.coordinator.camera_id_for(self.subentry_id)

    @property
    def camera(self) -> Camera | None:
        """The camera's latest merged state, or None if the engine lacks it."""
        camera_id = self.camera_id
        return self.coordinator.data.cameras.get(camera_id) if camera_id else None

    @property
    def available(self) -> bool:
        """Unavailable when the engine is unreachable or doesn't have the camera."""
        return super().available and self.camera is not None


@callback
def async_add_camera_entities(
    coordinator: CameraHubCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[CameraHubCoordinator, str], Iterable[Entity]],
    include: Callable[[Camera], bool] = lambda _camera: True,
) -> None:
    """Add entities for each camera subentry now and for ones added later.

    A camera is added once the engine reports it and it passes `include`;
    until then it is checked again on every refresh, so an engine upgrade
    that adds a capability gets its entities without a reload.
    """
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        subentries = camera_subentries(coordinator.config_entry)
        known.intersection_update(subentries)
        for subentry_id in subentries:
            if subentry_id in known:
                continue
            camera_id = coordinator.camera_id_for(subentry_id)
            camera = coordinator.data.cameras.get(camera_id) if camera_id else None
            if camera is None or not include(camera):
                continue
            known.add(subentry_id)
            async_add_entities(
                factory(coordinator, subentry_id), config_subentry_id=subentry_id
            )

    _add_new()
    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(_add_new))
