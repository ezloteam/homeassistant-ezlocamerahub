"""Tests for the engine control-plane client."""

from __future__ import annotations

from aiohttp import ClientError
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ezlo_camerahub.api import CameraHubClient
from custom_components.ezlo_camerahub.errors import (
    CameraHubConnectionError,
    CameraHubResponseError,
    InvalidResponseError,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import BASE, HOST, PORT, load_fixture


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> CameraHubClient:
    return CameraHubClient(async_get_clientsession(hass), HOST, PORT)


async def test_get_status(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(
        f"{BASE}/app/standalone/status", json=load_fixture("status.json")
    )
    status = await client.async_get_status()
    assert status.serial == "105299999"


async def test_get_cameras_and_uploads(
    client, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{BASE}/app/standalone/cameras", json=load_fixture("cameras.json")
    )
    aioclient_mock.get(
        f"{BASE}/app/standalone/uploads", json=load_fixture("uploads.json")
    )
    assert len(await client.async_get_cameras()) == 2
    assert len(await client.async_get_uploads()) == 3


async def test_unprovisioned_404_is_empty(
    client, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{BASE}/app/standalone/cameras", status=404)
    aioclient_mock.get(f"{BASE}/app/standalone/uploads", status=404)
    assert await client.async_get_cameras() == ()
    assert await client.async_get_uploads() == ()


async def test_connection_error(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/app/standalone/status", exc=ClientError("down"))
    with pytest.raises(CameraHubConnectionError):
        await client.async_get_status()


async def test_timeout_is_connection_error(
    client, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{BASE}/app/standalone/status", exc=TimeoutError())
    with pytest.raises(CameraHubConnectionError):
        await client.async_get_status()


async def test_http_error(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/app/standalone/status", status=500)
    with pytest.raises(CameraHubResponseError) as err:
        await client.async_get_status()
    assert err.value.status == 500


async def test_non_json(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/app/standalone/status", text="<html>")
    with pytest.raises(InvalidResponseError):
        await client.async_get_status()


async def test_set_mode(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras/cam_front/mode",
        json={"id": "cam_front", "mode": "continuous"},
    )
    await client.async_set_mode("cam_front", "continuous")
    assert aioclient_mock.mock_calls[-1][2] == {"mode": "continuous"}


async def test_set_mode_404(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{BASE}/app/standalone/cameras/cam_x/mode", status=404)
    with pytest.raises(CameraHubResponseError):
        await client.async_set_mode("cam_x", "motion")


@pytest.mark.parametrize("mode", ["", "MOTION", "off", "motion;"])
async def test_set_mode_rejects_invalid_mode(client, mode) -> None:
    with pytest.raises(ValueError):
        await client.async_set_mode("cam_front", mode)


@pytest.mark.parametrize("camera_id", ["", "../settings", "cam front", "cam/x"])
async def test_set_mode_rejects_invalid_camera_id(client, camera_id) -> None:
    with pytest.raises(ValueError):
        await client.async_set_mode(camera_id, "motion")


async def test_ipv6_base_url(hass: HomeAssistant) -> None:
    client = CameraHubClient(async_get_clientsession(hass), "fe80::1", 9997)
    assert client.base_url == "http://[fe80::1]:9997"


async def test_add_camera(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        f"{BASE}/app/standalone/cameras",
        status=201,
        json={"id": "cam_new", "name": "N"},
    )
    camera_id = await client.async_add_camera("N", "rtsp://u:p@h/s", "tcp", "motion")
    assert camera_id == "cam_new"
    assert aioclient_mock.mock_calls[-1][2] == {
        "name": "N",
        "rtsp_url": "rtsp://u:p@h/s",
        "transport": "tcp",
        "mode": "motion",
    }


@pytest.mark.parametrize("body", [{}, {"id": 5}, {"id": "../x"}, []])
async def test_add_camera_bad_response(
    client, aioclient_mock: AiohttpClientMocker, body
) -> None:
    aioclient_mock.post(f"{BASE}/app/standalone/cameras", status=201, json=body)
    with pytest.raises(InvalidResponseError):
        await client.async_add_camera("N", "rtsp://h/s", "auto", "continuous")


@pytest.mark.parametrize(
    ("transport", "mode"), [("quic", "continuous"), ("auto", "always")]
)
async def test_add_camera_rejects_invalid(client, transport, mode) -> None:
    with pytest.raises(ValueError):
        await client.async_add_camera("N", "rtsp://h/s", transport, mode)


async def test_delete_camera(client, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.delete(f"{BASE}/app/standalone/cameras/cam_front", json={})
    await client.async_delete_camera("cam_front")
    aioclient_mock.clear_requests()
    aioclient_mock.delete(f"{BASE}/app/standalone/cameras/cam_gone", status=404)
    await client.async_delete_camera("cam_gone")
    with pytest.raises(ValueError):
        await client.async_delete_camera("../settings")
