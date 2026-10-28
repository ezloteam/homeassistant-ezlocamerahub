"""Camera: the camera's own RTSP stream, read directly from the LAN.

Camera Hub live view is cloud-only WebRTC; the engine serves no local
player stream. Home Assistant therefore pulls the RTSP URL the camera was
added to the hub with (ideally the H.264 sub stream: H.265 main streams
won't play in most browsers) and grabs snapshots from it with ffmpeg.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image as ffmpeg_get_image
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import CameraHubConfigEntry, CameraHubCoordinator
from .entity import CameraDeviceEntity, async_add_camera_entities
from .rtsp import redact_rtsp_url

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CameraHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up cameras."""
    async_add_camera_entities(
        entry.runtime_data,
        async_add_entities,
        lambda coord, subentry_id: [HubCamera(coord, subentry_id)],
    )


class HubCamera(CameraDeviceEntity, Camera):
    """A Camera Hub camera."""

    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: CameraHubCoordinator, subentry_id: str) -> None:
        """Initialize."""
        CameraDeviceEntity.__init__(self, coordinator, subentry_id, "camera")
        Camera.__init__(self)
        camera = self.camera
        if camera is not None and camera.transport == "tcp":
            # The hub forces interleaved TCP for this camera (NAT); do the same.
            self.stream_options = {CONF_RTSP_TRANSPORT: "tcp"}

    @property
    def is_recording(self) -> bool:
        """Whether the hub is recording this camera right now."""
        camera = self.camera
        return bool(camera and camera.recording_now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Codec and the stream address with credentials removed."""
        camera = self.camera
        if camera is None:
            return {}
        return {"codec": camera.codec, "stream": redact_rtsp_url(camera.rtsp_url)}

    async def stream_source(self) -> str | None:
        """The camera's RTSP URL, verbatim (credentials included, never logged)."""
        camera = self.camera
        return camera.rtsp_url if camera and camera.rtsp_url else None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Grab a JPEG from the RTSP stream."""
        source = await self.stream_source()
        image = None
        if source:
            image = await ffmpeg_get_image(
                self.hass, source, width=width, height=height
            )
        if not image:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="snapshot_failed",
                translation_placeholders={"camera": self._device_name},
            )
        return image
