"""Check an Ezlo login before handing it to the engine.

The same request the engine makes for its first provision
(POST {api}/api/v4/login/1/sessions, OEM auto-resolved), so a wrong password
is reported in the config flow instead of as a provisioning failure later.
The password only goes to the Ezlo cloud over HTTPS and is never logged.
"""

from __future__ import annotations

import os
from typing import Final

from aiohttp import ClientError, ClientSession, ClientTimeout

from .engine import CLOUD_API_ENV

DEFAULT_API: Final = "https://api-cloud.ezlo.com"
SESSIONS_PATH: Final = "/api/v4/login/1/sessions"
OEM_IDS: Final = (1, 501)


class CannotConnect(Exception):
    """The Ezlo cloud could not be reached."""


class InvalidAuth(Exception):
    """The Ezlo cloud rejected the login."""


def api_base() -> str:
    """The Ezlo cloud API base (staging via the developer override)."""
    return os.environ.get(CLOUD_API_ENV) or DEFAULT_API


async def async_check_login(
    session: ClientSession, username: str, password: str
) -> None:
    """Raise InvalidAuth or CannotConnect unless the login works."""
    try:
        async with session.post(
            f"{api_base()}{SESSIONS_PATH}",
            json={
                "user_id": username,
                "user_password": password,
                "oem_ids": list(OEM_IDS),
            },
            timeout=ClientTimeout(total=20),
        ) as resp:
            if resp.status in (400, 401, 403):
                raise InvalidAuth
            if resp.status >= 300:
                raise CannotConnect(f"HTTP {resp.status}")
            body = await resp.json(content_type=None)
    except (ClientError, TimeoutError, ValueError) as err:
        raise CannotConnect(type(err).__name__) from err
    if not isinstance(body, dict) or not body.get("tokens"):
        raise InvalidAuth
