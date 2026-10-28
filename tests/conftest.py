"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Callable, Generator
import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ezlo_camerahub.const import DOMAIN
from homeassistant.core import HomeAssistant

pytest_plugins = ["pytest_homeassistant_custom_component"]

FIXTURES = Path(__file__).parent / "fixtures"
HOST = "127.0.0.1"
PORT = 19997
BASE = f"http://{HOST}:{PORT}"
USERNAME = "me@example.com"

FRONT = {
    "stream_url": "rtsp://10.0.9.64:554/Preview_01_sub",
    "username": "admin",
    "password": "s3cr@t",
    "transport": "tcp",
    "mode": "motion",
    "camera_id": "cam_front",
}
DRIVE = {
    "stream_url": "rtsp://10.0.9.65:554/Preview_01_main",
    "username": "",
    "password": "",
    "transport": "auto",
    "mode": "continuous",
    "camera_id": "cam_drive",
}


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture as a fresh dict."""
    return json.loads((FIXTURES / name).read_text())


def subentry_id(entry: MockConfigEntry, title: str) -> str:
    """The id of the camera subentry with this title."""
    return next(sid for sid, sub in entry.subentries.items() if sub.title == title)


class FakeSupervisor:
    """Stands in for the engine process; the engine's HTTP API is mocked."""

    instances: ClassVar[list[FakeSupervisor]] = []

    def __init__(self, hass, binary, cache_dir, credentials) -> None:
        self.cache_dir = cache_dir
        self.credentials = credentials
        self.port = PORT
        self.provisioned = True
        self.failures = 0
        self.last_error = None
        self.started = False
        self.stopped = False
        FakeSupervisor.instances.append(self)

    async def async_start(self) -> None:
        self.started = True

    async def async_stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> Generator[None]:
    """Load custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def fake_supervisor(monkeypatch: pytest.MonkeyPatch) -> type[FakeSupervisor]:
    """Replace the engine process with FakeSupervisor."""
    FakeSupervisor.instances = []
    monkeypatch.setattr(
        "custom_components.ezlo_camerahub.EngineSupervisor", FakeSupervisor
    )
    return FakeSupervisor


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A provisioned hub with two cameras."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ezlo Camera Hub",
        data={"username": USERNAME},
        subentries_data=[
            {
                "data": FRONT,
                "subentry_type": "camera",
                "title": "Front Door",
                "unique_id": "cam_front",
            },
            {
                "data": DRIVE,
                "subentry_type": "camera",
                "title": "Driveway",
                "unique_id": "cam_drive",
            },
        ],
    )


@pytest.fixture
def mock_engine(aioclient_mock: AiohttpClientMocker) -> Callable[..., None]:
    """Register engine responses; call again with overrides to change them."""

    def _register(
        status: dict[str, Any] | None = None,
        cameras: dict[str, Any] | None = None,
        uploads: dict[str, Any] | None = None,
    ) -> None:
        aioclient_mock.clear_requests()
        aioclient_mock.get(
            f"{BASE}/app/standalone/status", json=status or load_fixture("status.json")
        )
        aioclient_mock.get(
            f"{BASE}/app/standalone/cameras",
            json=cameras or load_fixture("cameras.json"),
        )
        aioclient_mock.get(
            f"{BASE}/app/standalone/uploads",
            json=uploads or load_fixture("uploads.json"),
        )

    _register()
    return _register


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine
) -> MockConfigEntry:
    """Set up the integration against the mocked engine."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
