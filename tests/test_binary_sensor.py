"""Tests for the binary sensors."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from .conftest import load_fixture


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_no_motion_sensor_from_recording(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    # `recording` is a mode-armed flag (steadily true in motion mode), not a
    # motion signal, so no motion sensor is derived from it.
    assert hass.states.get("binary_sensor.front_door_motion") is None
    assert hass.states.get("binary_sensor.driveway_motion") is None


async def test_cloud_connected(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    entity_id = "binary_sensor.ezlo_camera_hub_cloud_connection"
    assert hass.states.get(entity_id).state == STATE_ON
    status = load_fixture("status.json")
    status["nma"]["connected"] = False
    mock_engine(status=status)
    await _refresh(hass, setup_integration)
    assert hass.states.get(entity_id).state == STATE_OFF


def _status_with_motion(
    front_active: bool = False, front_ts: int | None = None
) -> dict:
    """A /status from an engine that reports motion_active / last_motion_ts."""
    status = load_fixture("status.json")
    status["cameras"]["cam_front"].update(
        motion_active=front_active, last_motion_ts=front_ts
    )
    status["cameras"]["cam_drive"].update(motion_active=False, last_motion_ts=None)
    return status


async def test_motion_sensor_follows_motion_active(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine
) -> None:
    mock_engine(status=_status_with_motion())
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.front_door_motion")
    assert state.state == STATE_OFF
    assert state.attributes["device_class"] == "motion"
    assert hass.states.get("binary_sensor.driveway_motion").state == STATE_OFF

    mock_engine(status=_status_with_motion(True, 1761580700))
    await _refresh(hass, config_entry)
    assert hass.states.get("binary_sensor.front_door_motion").state == STATE_ON

    mock_engine(status=_status_with_motion(False, 1761580700))
    await _refresh(hass, config_entry)
    assert hass.states.get("binary_sensor.front_door_motion").state == STATE_OFF


async def test_motion_between_polls_pulses_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine
) -> None:
    mock_engine(status=_status_with_motion(False, 1761580700))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    # An already-known last motion is not a new event.
    assert hass.states.get("binary_sensor.front_door_motion").state == STATE_OFF

    # A trip that started and ended between two polls: only the timestamp moved.
    mock_engine(status=_status_with_motion(False, 1761580800))
    await _refresh(hass, config_entry)
    assert hass.states.get("binary_sensor.front_door_motion").state == STATE_ON

    mock_engine(status=_status_with_motion(False, 1761580800))
    await _refresh(hass, config_entry)
    assert hass.states.get("binary_sensor.front_door_motion").state == STATE_OFF


async def test_first_ever_motion_between_polls_pulses_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine
) -> None:
    mock_engine(status=_status_with_motion(False, None))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_engine(status=_status_with_motion(False, 1761580800))
    await _refresh(hass, config_entry)
    assert hass.states.get("binary_sensor.front_door_motion").state == STATE_ON


async def test_motion_sensor_appears_after_engine_upgrade(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    assert hass.states.get("binary_sensor.front_door_motion") is None
    mock_engine(status=_status_with_motion())
    await _refresh(hass, setup_integration)
    assert hass.states.get("binary_sensor.front_door_motion").state == STATE_OFF
