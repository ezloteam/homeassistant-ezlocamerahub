"""Tests for the Ezlo login check."""

from __future__ import annotations

from aiohttp import ClientError
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ezlo_camerahub.engine import CLOUD_API_ENV
from custom_components.ezlo_camerahub.ezlo_cloud import (
    CannotConnect,
    InvalidAuth,
    async_check_login,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

LIVE = "https://api-cloud.ezlo.com/api/v4/login/1/sessions"


async def test_login_ok(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(LIVE, json={"tokens": [{"token": "jwt", "oem_id": 501}]})
    await async_check_login(async_get_clientsession(hass), "me", "pw")
    assert aioclient_mock.mock_calls[0][2] == {
        "user_id": "me",
        "user_password": "pw",
        "oem_ids": [1, 501],
    }


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401, "json": {"error": "invalid_credentials"}}, InvalidAuth),
        ({"status": 400}, InvalidAuth),
        ({"json": {"tokens": []}}, InvalidAuth),
        ({"status": 503}, CannotConnect),
        ({"exc": ClientError()}, CannotConnect),
        ({"text": "<html>"}, CannotConnect),
    ],
)
async def test_login_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs, error
) -> None:
    aioclient_mock.post(LIVE, **kwargs)
    with pytest.raises(error):
        await async_check_login(async_get_clientsession(hass), "me", "pw")


async def test_staging_override(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CLOUD_API_ENV, "https://api-cloud-stg.ezlo.com")
    aioclient_mock.post(
        "https://api-cloud-stg.ezlo.com/api/v4/login/1/sessions",
        json={"tokens": [{"token": "t"}]},
    )
    await async_check_login(async_get_clientsession(hass), "me", "pw")
