"""Tests for setup, the engine lifecycle, provisioning and camera sync."""

from __future__ import annotations

from aiohttp import ClientError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ezlo_camerahub import storage_dir
from custom_components.ezlo_camerahub.engine import EngineStartError
from custom_components.ezlo_camerahub.engine_binary import NoEnginePinned
from custom_components.ezlo_camerahub.models import CameraConfig
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import BASE, DRIVE, USERNAME, FakeSupervisor, load_fixture, subentry_id


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    supervisor = FakeSupervisor.instances[-1]
    assert supervisor.started
    assert supervisor.cache_dir == storage_dir(hass) / entry.entry_id / "engine-cache"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert supervisor.stopped


async def test_engine_stopped_on_home_assistant_stop(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert FakeSupervisor.instances[-1].stopped


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (EngineStartError("no"), ConfigEntryState.SETUP_RETRY),
        (OSError("exec format error"), ConfigEntryState.SETUP_RETRY),
        (NoEnginePinned("none"), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_engine_start_failure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_engine,
    monkeypatch: pytest.MonkeyPatch,
    error,
    state,
) -> None:
    async def _fail(self) -> None:
        raise error

    monkeypatch.setattr(FakeSupervisor, "async_start", _fail)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is state


async def test_first_refresh_failure_stops_engine(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.get(f"{BASE}/app/standalone/status", exc=ClientError())
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert FakeSupervisor.instances[-1].stopped


async def test_devices(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    entry = setup_integration
    devices = {
        identifier: device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for identifier in device.identifiers
    }
    hub = devices[("ezlo_camerahub", entry.entry_id)]
    assert hub.manufacturer == "Ezlo"
    assert hub.serial_number == "105299999"
    front = devices[("ezlo_camerahub", subentry_id(entry, "Front Door"))]
    assert front.name == "Front Door"
    assert front.via_device_id == hub.id


async def test_no_deprecated_via_device(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert "deprecated `via_device`" not in caplog.text


async def test_entities_unavailable_when_engine_drops(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    assert hass.states.get("sensor.front_door_status").state == "online"
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/app/standalone/status", exc=ClientError())
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.front_door_status").state == STATE_UNAVAILABLE


async def test_password_dropped_once_provisioned(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={"username": USERNAME, "password": "pw"}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert dict(config_entry.data) == {"username": USERNAME}


async def test_provisioning_in_progress_keeps_password(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={"username": USERNAME, "password": "pw"}
    )
    mock_engine(status={"provisioned": False, "last_error": None})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data["password"] == "pw"
    assert FakeSupervisor.instances[-1].credentials() == (USERNAME, "pw")


@pytest.mark.parametrize(
    ("data", "last_error"),
    [
        (
            {"username": USERNAME, "password": "wrong"},
            "login failed: HTTP 401 (invalid_credentials)",
        ),
        ({"username": USERNAME}, "needs credentials for the FIRST provision"),
    ],
)
async def test_provisioning_needs_login(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_engine, data, last_error
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, data=data)
    mock_engine(status={"provisioned": False, "last_error": last_error})
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    assert FakeSupervisor.instances[-1].stopped
    if "password" not in data:
        assert FakeSupervisor.instances[-1].credentials() is None


class StatefulCameras:
    """Engine camera list that add/delete really change."""

    def __init__(self, ids: list[str]) -> None:
        self.cameras = {
            i: CameraConfig(i, i, "rtsp://h/p", None, "continuous", None) for i in ids
        }
        self.added: list[tuple[str, str, str, str]] = []
        self.deleted: list[str] = []

    async def async_get_cameras(self):
        return tuple(self.cameras.values())

    async def async_add_camera(self, name, url, transport, mode):
        new_id = f"cam_new{len(self.added)}"
        self.added.append((name, url, transport, mode))
        self.cameras[new_id] = CameraConfig(new_id, name, url, None, mode, transport)
        return new_id

    async def async_delete_camera(self, camera_id):
        self.deleted.append(camera_id)
        self.cameras.pop(camera_id, None)


async def test_sync_readds_missing_and_deletes_strays(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = setup_integration.runtime_data
    engine = StatefulCameras(["cam_front", "cam_stray"])
    for name in ("async_get_cameras", "async_add_camera", "async_delete_camera"):
        monkeypatch.setattr(coordinator.client, name, getattr(engine, name))
    await coordinator.async_sync_cameras()
    await hass.async_block_till_done()
    assert engine.added == [
        ("Driveway", "rtsp://10.0.9.65:554/Preview_01_main", "auto", "continuous")
    ]
    assert engine.deleted == ["cam_stray"]
    drive = setup_integration.subentries[subentry_id(setup_integration, "Driveway")]
    assert drive.data["camera_id"] == "cam_new0"
    assert drive.unique_id == "cam_new0"
    # Idempotent: nothing more to do.
    await coordinator.async_sync_cameras()
    assert len(engine.added) == 1


async def test_sync_composes_credentials(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = setup_integration.runtime_data
    engine = StatefulCameras(["cam_drive"])
    for name in ("async_get_cameras", "async_add_camera", "async_delete_camera"):
        monkeypatch.setattr(coordinator.client, name, getattr(engine, name))
    await coordinator.async_sync_cameras()
    await hass.async_block_till_done()
    assert engine.added[0][1] == "rtsp://admin:s3cr%40t@10.0.9.64:554/Preview_01_sub"


async def test_sync_error_retried(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_engine,
) -> None:
    coordinator = setup_integration.runtime_data
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/app/standalone/cameras", exc=ClientError())
    await coordinator.async_sync_cameras()
    assert coordinator._synced is False
    mock_engine()
    await coordinator.async_refresh()
    assert coordinator._synced is True


async def test_removing_subentry_deletes_camera(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = setup_integration
    aioclient_mock.delete(f"{BASE}/app/standalone/cameras/cam_drive", json={})
    sid = subentry_id(entry, "Driveway")
    assert hass.states.get("sensor.driveway_status") is not None
    hass.config_entries.async_remove_subentry(entry, sid)
    await hass.async_block_till_done()
    assert any(
        c[0] == "DELETE" and str(c[1]).endswith("/cam_drive")
        for c in aioclient_mock.mock_calls
    )
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", "ezlo_camerahub", f"{sid}_status"
        )
        is None
    )


async def test_adding_subentry_adds_entities(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    cameras = load_fixture("cameras.json")
    cameras["cameras"].append(
        {"id": "cam_back", "name": "Back Yard", "rtsp_url": "rtsp://h/p"}
    )
    mock_engine(cameras=cameras)
    hass.config_entries.async_add_subentry(
        setup_integration,
        ConfigSubentry(
            data={**DRIVE, "camera_id": "cam_back", "stream_url": "rtsp://h/p"},
            subentry_type="camera",
            title="Back Yard",
            unique_id="cam_back",
        ),
    )
    await hass.async_block_till_done()
    assert hass.states.get("camera.back_yard") is not None
    assert hass.states.get("sensor.back_yard_status").state == "offline"


async def test_camera_missing_on_engine_is_unavailable(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    cameras = load_fixture("cameras.json")
    cameras["cameras"] = cameras["cameras"][:1]
    mock_engine(cameras=cameras)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.driveway_status").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.front_door_status").state == "online"


async def test_remove_entry_deletes_engine_storage(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    cache = storage_dir(hass) / setup_integration.entry_id / "engine-cache"
    await hass.async_add_executor_job(lambda: cache.mkdir(parents=True))
    assert await hass.config_entries.async_remove(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert not await hass.async_add_executor_job(cache.parent.exists)
