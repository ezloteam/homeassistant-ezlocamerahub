"""Tests for the recording mode select."""

from __future__ import annotations

from aiohttp import ClientError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import BASE, load_fixture

FRONT = "select.front_door_recording_mode"
DRIVE = "select.driveway_recording_mode"


async def test_options_gated_on_motion_capable(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    front = hass.states.get(FRONT)
    assert front.state == "motion"
    assert front.attributes[ATTR_OPTIONS] == ["continuous", "motion"]
    drive = hass.states.get(DRIVE)
    assert drive.state == "continuous"
    assert drive.attributes[ATTR_OPTIONS] == ["continuous"]


async def test_select_posts_mode_and_refreshes(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_engine,
) -> None:
    status = load_fixture("status.json")
    status["cameras"]["cam_front"]["mode"] = "continuous"
    mock_engine(status=status)
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras/cam_front/mode",
        json={"id": "cam_front", "mode": "continuous"},
    )
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: FRONT, ATTR_OPTION: "continuous"},
        blocking=True,
    )
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posts[-1][2] == {"mode": "continuous"}
    assert hass.states.get(FRONT).state == "continuous"


async def test_select_motion_on_incapable_camera_rejected(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: DRIVE, ATTR_OPTION: "motion"},
            blocking=True,
        )


async def test_select_engine_error(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras/cam_front/mode", exc=ClientError()
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: FRONT, ATTR_OPTION: "continuous"},
            blocking=True,
        )


async def test_no_select_without_motion_capability(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine
) -> None:
    status = load_fixture("status.json")
    status["capabilities"]["motion"] = False
    mock_engine(status=status)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(FRONT) is None
    assert hass.states.get("sensor.front_door_recording_mode").state == "motion"
