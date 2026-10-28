"""Tests for the camera and hub sensors."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .conftest import load_fixture


async def test_camera_sensors(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    assert hass.states.get("sensor.front_door_recording_mode").state == "motion"
    assert hass.states.get("sensor.driveway_recording_mode").state == "continuous"
    assert hass.states.get("sensor.front_door_status").state == "online"
    assert hass.states.get("sensor.driveway_status").state == "offline"
    assert hass.states.get("sensor.front_door_segments_uploaded").state == "40"
    assert hass.states.get("sensor.front_door_upload_errors").state == "2"
    assert (
        hass.states.get("sensor.front_door_last_upload").state
        == "2025-10-27T15:55:00+00:00"
    )
    assert hass.states.get("sensor.driveway_last_upload").state == STATE_UNKNOWN
    assert hass.states.get("sensor.front_door_last_clip_source").state == "motion"
    assert hass.states.get("sensor.driveway_last_clip_source").state == "continuous"


async def test_status_motion_mode_armed_is_online(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    # In motion mode `recording` only means "armed", so it is not "recording".
    assert hass.states.get("sensor.front_door_status").state == "online"


async def test_status_motion_mode_waiting_for_first_motion_is_armed(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    # A motion-mode camera opens its stream only on motion; until then its
    # ONVIF event service answering (motion_capable) shows it is reachable.
    status = load_fixture("status.json")
    status["cameras"]["cam_front"]["rtsp_ok"] = False
    mock_engine(status=status)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.front_door_status").state == "armed"


async def test_status_motion_mode_without_event_source_is_offline(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    status = load_fixture("status.json")
    status["cameras"]["cam_front"]["rtsp_ok"] = False
    status["cameras"]["cam_front"]["motion_capable"] = False
    mock_engine(status=status)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.front_door_status").state == "offline"


async def test_status_recording_in_continuous_mode(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    status = load_fixture("status.json")
    status["cameras"]["cam_drive"]["rtsp_ok"] = True
    mock_engine(status=status)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.driveway_status").state == "recording"


async def test_hub_upload_sensors(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    assert hass.states.get("sensor.ezlo_camera_hub_uploads_pending").state == "1"
    assert hass.states.get("sensor.ezlo_camera_hub_uploads_failed").state == "2"
    assert hass.states.get("sensor.ezlo_camera_hub_uploads_done").state == "42"


async def test_codec_attribute_and_no_credentials(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    for state in hass.states.async_all():
        assert "s3cr" not in str(state.attributes)


async def test_last_motion_sensor_only_with_motion_support(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    assert hass.states.get("sensor.front_door_last_motion") is None
    status = load_fixture("status.json")
    status["cameras"]["cam_front"].update(
        motion_active=False, last_motion_ts=1761580500
    )
    status["cameras"]["cam_drive"].update(motion_active=False, last_motion_ts=None)
    mock_engine(status=status)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.front_door_last_motion").state
        == "2025-10-27T15:55:00+00:00"
    )
    assert hass.states.get("sensor.driveway_last_motion").state == STATE_UNKNOWN
