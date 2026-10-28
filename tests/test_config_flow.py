"""Tests for the config flow and the camera subentry flow."""

from __future__ import annotations

from aiohttp import ClientError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ezlo_camerahub import config_flow
from custom_components.ezlo_camerahub.const import DOMAIN
from custom_components.ezlo_camerahub.engine_binary import (
    OVERRIDE_ENV,
    UnsupportedPlatform,
)
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BASE, USERNAME, load_fixture, subentry_id

LOGIN = "https://api-cloud.ezlo.com/api/v4/login/1/sessions"
LOGIN_OK = {"tokens": [{"token": "jwt", "oem_id": 501}]}


@pytest.fixture(autouse=True)
def linux_amd64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(config_flow, "engine_arch", lambda: "amd64")


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_engine
) -> None:
    aioclient_mock.post(LOGIN, json=LOGIN_OK)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": f" {USERNAME} ", "password": "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Ezlo Camera Hub"
    assert result["data"] == {"username": USERNAME, "password": "pw"}
    login = next(c for c in aioclient_mock.mock_calls if str(c[1]) == LOGIN)
    assert login[2] == {"user_id": USERNAME, "user_password": "pw", "oem_ids": [1, 501]}


async def test_username_is_plain_text(hass: HomeAssistant) -> None:
    """Ezlo usernames aren't email addresses; the field must accept any name."""
    result = await _start(hass)
    selector = result["data_schema"].schema["username"]
    assert selector.config["type"] == "text"
    assert selector.config["autocomplete"] == "username"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401, "json": {"error": "invalid_credentials"}}, "invalid_auth"),
        ({"exc": ClientError()}, "cannot_connect"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_engine, kwargs, error
) -> None:
    aioclient_mock.post(LOGIN, **kwargs)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": "pw"}
    )
    assert result["errors"] == {"base": error}
    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN, json=LOGIN_OK)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*_args):
        raise RuntimeError

    monkeypatch.setattr(config_flow, "async_check_login", _boom)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": "pw"}
    )
    assert result["errors"] == {"base": "unknown"}


async def test_unsupported_platform(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unsupported():
        raise UnsupportedPlatform("darwin")

    monkeypatch.setattr(config_flow, "engine_arch", _unsupported)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_platform"


async def test_override_skips_platform_check(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unsupported():
        raise UnsupportedPlatform("darwin")

    monkeypatch.setattr(config_flow, "engine_arch", _unsupported)
    monkeypatch.setenv(OVERRIDE_ENV, "/opt/ha-camerasd")
    await _start(hass)


async def test_single_instance(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = setup_integration
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    aioclient_mock.post(LOGIN, status=401)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN, json=LOGIN_OK)
    # The reload after reauth talks to the engine again.
    for path, name in (
        ("status", "status.json"),
        ("cameras", "cameras.json"),
        ("uploads", "uploads.json"),
    ):
        aioclient_mock.get(f"{BASE}/app/standalone/{path}", json=load_fixture(name))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": "good"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


CAMERA_INPUT = {
    "name": "Garage",
    "stream_url": "rtsp://bob:p%40ss%23@10.0.0.5:554/sub",
    "username": "",
    "password": "",
    "transport": "tcp",
    "mode": "continuous",
}


async def _start_camera(hass: HomeAssistant, entry: MockConfigEntry):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "camera"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return result


async def test_add_camera(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras", status=201, json={"id": "cam_garage"}
    )
    result = await _start_camera(hass, setup_integration)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], CAMERA_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    post = next(c for c in aioclient_mock.mock_calls if c[0] == "POST")
    assert post[2] == {
        "name": "Garage",
        "rtsp_url": "rtsp://bob:p%40ss%23@10.0.0.5:554/sub",
        "transport": "tcp",
        "mode": "continuous",
    }
    sub = setup_integration.subentries[subentry_id(setup_integration, "Garage")]
    assert dict(sub.data) == {
        "stream_url": "rtsp://10.0.0.5:554/sub",
        "username": "bob",
        "password": "p@ss#",
        "transport": "tcp",
        "mode": "continuous",
        "camera_id": "cam_garage",
    }
    assert sub.unique_id == "cam_garage"


async def test_add_camera_fields_win_over_url(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras", status=201, json={"id": "cam_x"}
    )
    result = await _start_camera(hass, setup_integration)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **CAMERA_INPUT,
            "stream_url": "rtsp://10.0.0.5/sub",
            "username": "al",
            "password": "a:b/c",
        },
    )
    post = next(c for c in aioclient_mock.mock_calls if c[0] == "POST")
    assert post[2]["rtsp_url"] == "rtsp://al:a%3Ab%2Fc@10.0.0.5/sub"


@pytest.mark.parametrize(
    ("change", "errors"),
    [
        ({"stream_url": "http://10.0.0.5/x"}, {"stream_url": "invalid_stream_url"}),
        ({"stream_url": "rtsp:///nohost"}, {"stream_url": "invalid_stream_url"}),
        ({"name": "  "}, {"name": "name_required"}),
    ],
)
async def test_add_camera_validation(
    hass: HomeAssistant, setup_integration: MockConfigEntry, change, errors
) -> None:
    result = await _start_camera(hass, setup_integration)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**CAMERA_INPUT, **change}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    assert "s3cr" not in str(result) and "p@ss" not in str(result.get("data_schema"))


async def test_add_camera_engine_error(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.post(f"{BASE}/app/standalone/cameras", status=500)
    result = await _start_camera(hass, setup_integration)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], CAMERA_INPUT
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_add_camera_before_provisioned(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_engine
) -> None:
    mock_engine(status={"provisioned": False, "last_error": None})
    hass.config_entries.async_update_entry(
        setup_integration, data={"username": USERNAME, "password": "pw"}
    )
    await hass.async_block_till_done()
    await setup_integration.runtime_data.async_refresh()
    result = await _start_camera(hass, setup_integration)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], CAMERA_INPUT
    )
    assert result["errors"] == {"base": "not_ready"}


async def test_add_camera_entry_not_loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await _start_camera(hass, config_entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], CAMERA_INPUT
    )
    assert result["errors"] == {"base": "not_ready"}


async def test_reconfigure_camera_keeps_password(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = setup_integration
    sid = subentry_id(entry, "Front Door")
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras", status=201, json={"id": "cam_front2"}
    )
    aioclient_mock.delete(f"{BASE}/app/standalone/cameras/cam_front", json={})
    result = await entry.start_subentry_reconfigure_flow(hass, sid)
    assert result["step_id"] == "reconfigure"
    assert "s3cr" not in str(result)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Front Porch",
            "stream_url": "rtsp://10.0.9.64:554/Preview_01_sub",
            "username": "admin",
            "password": "",
            "transport": "tcp",
            "mode": "motion",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    post = next(c for c in aioclient_mock.mock_calls if c[0] == "POST")
    assert post[2]["rtsp_url"] == "rtsp://admin:s3cr%40t@10.0.9.64:554/Preview_01_sub"
    assert any(
        c[0] == "DELETE" and str(c[1]).endswith("/cam_front")
        for c in aioclient_mock.mock_calls
    )
    sub = entry.subentries[sid]
    assert sub.title == "Front Porch"
    assert sub.data["camera_id"] == "cam_front2"
    assert sub.data["password"] == "s3cr@t"


async def test_reconfigure_delete_failure_is_tolerated(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = setup_integration
    sid = subentry_id(entry, "Driveway")
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras", status=201, json={"id": "cam_drive2"}
    )
    aioclient_mock.delete(f"{BASE}/app/standalone/cameras/cam_drive", exc=ClientError())
    result = await entry.start_subentry_reconfigure_flow(hass, sid)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**CAMERA_INPUT, "name": "Driveway", "stream_url": "rtsp://10.0.9.65/x"},
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[sid].data["camera_id"] == "cam_drive2"
