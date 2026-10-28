"""Tests for the camera entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.camera import async_get_image, async_get_stream_source
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import load_fixture


async def test_camera_entity(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    state = hass.states.get("camera.front_door")
    assert state is not None
    assert state.attributes["codec"] == "h264"
    assert state.attributes["stream"] == "rtsp://***@10.0.9.64:554/Preview_01_sub"
    assert "s3cr" not in str(state.attributes)


async def test_stream_source_passes_url_verbatim(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    # The engine and cameras expect userinfo exactly as stored; never re-encode.
    source = await async_get_stream_source(hass, "camera.front_door")
    assert source == "rtsp://admin:s3cr%40t@10.0.9.64:554/Preview_01_sub"


async def test_snapshot_via_ffmpeg(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    with patch(
        "custom_components.ezlo_camerahub.camera.ffmpeg_get_image",
        AsyncMock(return_value=b"jpeg"),
    ) as grab:
        image = await async_get_image(hass, "camera.front_door")
    assert image.content == b"jpeg"
    assert (
        grab.await_args.args[1] == "rtsp://admin:s3cr%40t@10.0.9.64:554/Preview_01_sub"
    )


async def test_snapshot_failure(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    with (
        patch(
            "custom_components.ezlo_camerahub.camera.ffmpeg_get_image",
            AsyncMock(return_value=None),
        ),
        pytest.raises(HomeAssistantError),
    ):
        await async_get_image(hass, "camera.front_door")


async def test_recording_only_in_continuous_mode_with_ingest(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    # Continuous mode reports recording even while RTSP ingest is down, and
    # motion mode reports recording whenever armed: neither is "recording".
    assert hass.states.get("camera.driveway").state == "idle"
    assert hass.states.get("camera.front_door").state == "idle"
    status = load_fixture("status.json")
    status["cameras"]["cam_drive"]["rtsp_ok"] = True
    mock_engine(status=status)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("camera.driveway").state == "recording"
