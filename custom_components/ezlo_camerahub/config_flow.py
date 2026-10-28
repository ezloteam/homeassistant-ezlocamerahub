"""Config flow: sign in with Ezlo; cameras are added as subentries."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CAMERA_ID,
    CONF_MODE,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_STREAM_URL,
    CONF_TRANSPORT,
    CONF_USERNAME,
    DOMAIN,
    MODE_CONTINUOUS,
    MODES,
    SUBENTRY_CAMERA,
    TRANSPORT_AUTO,
    TRANSPORTS,
)
from .engine_binary import UnsupportedPlatform, engine_arch, override_path
from .errors import CameraHubError
from .ezlo_cloud import CannotConnect, InvalidAuth, async_check_login
from .rtsp import InvalidStreamUrl, compose_rtsp_url, split_rtsp_url

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


def _login_schema(username: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=username): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
            ),
            vol.Required(CONF_PASSWORD): _PASSWORD,
        }
    )


class CameraHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the Camera Hub that runs inside Home Assistant."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Cameras are subentries."""
        return {SUBENTRY_CAMERA: CameraSubentryFlow}

    async def _async_login_errors(self, username: str, password: str) -> dict[str, str]:
        try:
            await async_check_login(
                async_get_clientsession(self.hass), username, password
            )
        except InvalidAuth:
            return {"base": "invalid_auth"}
        except CannotConnect:
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error checking the Ezlo login")
            return {"base": "unknown"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Ezlo login the hub provisions with."""
        if override_path() is None:
            try:
                engine_arch()
            except UnsupportedPlatform:
                return self.async_abort(reason="unsupported_platform")
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            errors = await self._async_login_errors(username, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(
                    title="Ezlo Camera Hub",
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_login_schema((user_input or {}).get(CONF_USERNAME, "")),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The engine needs the password again to provision."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            errors = await self._async_login_errors(username, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_login_schema(entry.data.get(CONF_USERNAME, "")),
            errors=errors,
        )


def _camera_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(
                CONF_STREAM_URL, default=defaults.get(CONF_STREAM_URL, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
            vol.Optional(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            # Never prefilled; left empty while editing keeps the stored one.
            vol.Optional(CONF_PASSWORD, default=""): _PASSWORD,
            vol.Required(
                CONF_TRANSPORT, default=defaults.get(CONF_TRANSPORT, TRANSPORT_AUTO)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(TRANSPORTS),
                    translation_key=CONF_TRANSPORT,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_MODE, default=defaults.get(CONF_MODE, MODE_CONTINUOUS)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(MODES),
                    translation_key=CONF_MODE,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


class CameraSubentryFlow(ConfigSubentryFlow):
    """Add or edit a camera on the hub."""

    def _coordinator(self):
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return None
        return entry.runtime_data

    def _camera_data(
        self, user_input: dict[str, Any], stored: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validated subentry data; raises InvalidStreamUrl."""
        bare, url_user, url_password = split_rtsp_url(user_input[CONF_STREAM_URL])
        username = user_input.get(CONF_USERNAME, "").strip() or url_user
        password = (
            user_input.get(CONF_PASSWORD, "")
            or url_password
            or stored.get(CONF_PASSWORD, "")
        )
        return {
            CONF_STREAM_URL: bare,
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
            CONF_TRANSPORT: user_input[CONF_TRANSPORT],
            CONF_MODE: user_input[CONF_MODE],
        }

    async def _async_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        stored: Mapping[str, Any],
        title: str,
    ) -> tuple[dict[str, Any] | None, str, SubentryFlowResult | None]:
        """Validate and add the camera to the engine; returns (data, name, form)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            coordinator = self._coordinator()
            if coordinator is None or not coordinator.data.status.provisioned:
                errors["base"] = "not_ready"
            else:
                name = user_input[CONF_NAME].strip()
                try:
                    data = self._camera_data(user_input, stored)
                except InvalidStreamUrl:
                    errors[CONF_STREAM_URL] = "invalid_stream_url"
                else:
                    if not name:
                        errors[CONF_NAME] = "name_required"
                    else:
                        url = compose_rtsp_url(
                            data[CONF_STREAM_URL],
                            data[CONF_USERNAME],
                            data[CONF_PASSWORD],
                        )
                        try:
                            camera_id = await coordinator.client.async_add_camera(
                                name, url, data[CONF_TRANSPORT], data[CONF_MODE]
                            )
                        except CameraHubError:
                            errors["base"] = "cannot_connect"
                        else:
                            return {**data, CONF_CAMERA_ID: camera_id}, name, None
        defaults = {**stored, CONF_NAME: title, **(user_input or {})}
        form = self.async_show_form(
            step_id=step_id,
            data_schema=_camera_schema(defaults),
            errors=errors,
        )
        return None, "", form

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a camera."""
        data, name, form = await self._async_form("user", user_input, {}, "")
        if data is None:
            assert form is not None
            return form
        return self.async_create_entry(
            title=name, data=data, unique_id=data[CONF_CAMERA_ID]
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a camera: the engine re-adds it, then the old one is removed."""
        sub = self._get_reconfigure_subentry()
        data, name, form = await self._async_form(
            "reconfigure", user_input, sub.data, sub.title
        )
        if data is None:
            assert form is not None
            return form
        old_id = sub.data.get(CONF_CAMERA_ID)
        coordinator = self._coordinator()
        if old_id and coordinator is not None:
            try:
                await coordinator.client.async_delete_camera(old_id)
            except (CameraHubError, ValueError) as err:
                # The next sync deletes it: no subentry refers to it any more.
                _LOGGER.warning(
                    "Could not remove the camera's previous engine entry: %s", err
                )
        return self.async_update_and_abort(
            self._get_entry(),
            sub,
            title=name,
            data=data,
            unique_id=data[CONF_CAMERA_ID],
        )
