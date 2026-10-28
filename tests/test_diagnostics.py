"""Tests for diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def test_diagnostics_redact_credentials(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_integration: MockConfigEntry,
) -> None:
    assert await async_setup_component(hass, "diagnostics", {})
    diag = await get_diagnostics_for_config_entry(hass, hass_client, setup_integration)
    text = str(diag)
    assert "s3cr" not in text
    assert "admin" not in text
    assert "aa:bb:cc:dd:ee:ff" not in text
    assert (
        diag["cameras"]["cam_front"]["stream"]
        == "rtsp://***@10.0.9.64:554/Preview_01_sub"
    )
    assert diag["status"]["provisioned"] is True
