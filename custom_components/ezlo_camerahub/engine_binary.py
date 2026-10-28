"""Obtain and verify the pinned ha-camerasd engine binary.

Each integration release pins one engine version and the SHA-256 of its
static Linux build per architecture. The binary is downloaded once into the
integration's storage directory and its hash is checked before every start,
so a binary changed on disk is never run.

For development, EZLO_CAMERAHUB_ENGINE_BINARY names a local binary to run
instead (no download, no hash check).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import platform
import sys
from typing import Final

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# Published engine builds: {ENGINE_BASE_URL}/{tag}/ha-camerasd-{triple} (dl.mios.com,
# anonymous https). The pinned SHA-256 is the authority: the .sha256 published
# next to each binary comes from the same host, so it isn't fetched.
# ENGINE_VERSION (the release tag) and ENGINE_SHA256 (by arch) are copied from
# the tag's MANIFEST.txt when bumping the engine.
ENGINE_BASE_URL: Final[str | None] = (
    "https://dl.mios.com/linux_firmware/build/custom_packages/ha-camerasd/standalone"
)
ENGINE_VERSION: Final[str | None] = "v0.3.0"
ENGINE_SHA256: Final[dict[str, str]] = {
    "amd64": "946c879a2caf66f8be434d4efd840bf9e9e4a81e9f2b5322ba72c0b8fa18322c",
    "arm64": "897b5e78f07dc11811d26af05656c3f8b476f1fe845aff2359daeff1b0839984",
}

OVERRIDE_ENV: Final = "EZLO_CAMERAHUB_ENGINE_BINARY"
_MACHINE_TO_ARCH: Final = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}
_ARCH_TO_TRIPLE: Final = {
    "amd64": "x86_64-unknown-linux-musl",
    "arm64": "aarch64-unknown-linux-musl",
}
_MAX_BINARY_BYTES: Final = 128 * 1024 * 1024
_DOWNLOAD_TIMEOUT: Final = 300
_CHUNK_BYTES: Final = 64 * 1024


class EngineBinaryError(Exception):
    """The engine binary can't be obtained or failed verification."""


class UnsupportedPlatform(EngineBinaryError):
    """No engine build exists for this operating system or CPU."""


class NoEnginePinned(EngineBinaryError):
    """This release of the integration doesn't pin a downloadable engine."""


class VerificationFailed(EngineBinaryError):
    """The downloaded binary doesn't match its pinned hash."""


@dataclass(frozen=True, slots=True)
class EnginePin:
    """A pinned engine release."""

    base_url: str | None = ENGINE_BASE_URL
    version: str | None = ENGINE_VERSION
    sha256: tuple[tuple[str, str], ...] = tuple(ENGINE_SHA256.items())

    def hash_for(self, arch: str) -> str | None:
        """Return the pinned SHA-256 for an architecture."""
        return dict(self.sha256).get(arch)


def engine_arch(machine: str | None = None, system: str | None = None) -> str:
    """Return the engine build name for this machine."""
    if (system or sys.platform) != "linux":
        raise UnsupportedPlatform(system or sys.platform)
    machine = (machine or platform.machine()).lower()
    try:
        return _MACHINE_TO_ARCH[machine]
    except KeyError as err:
        raise UnsupportedPlatform(machine) from err


def engine_url(base_url: str, version: str, arch: str) -> str:
    """The published binary for a release tag and architecture."""
    return f"{base_url}/{version}/ha-camerasd-{_ARCH_TO_TRIPLE[arch]}"


def override_path() -> Path | None:
    """The developer override binary, if one is set."""
    value = os.environ.get(OVERRIDE_ENV)
    return Path(value) if value else None


class EngineBinary:
    """The engine binary on disk, kept at the pinned version."""

    def __init__(
        self, hass: HomeAssistant, bin_dir: Path, pin: EnginePin | None = None
    ) -> None:
        """Initialize."""
        self._hass = hass
        self._bin_dir = bin_dir
        self._pin = pin or EnginePin()

    async def async_ensure(self) -> Path:
        """Return the path of a runnable engine, downloading it if needed."""
        override = override_path()
        if override is not None:
            _LOGGER.warning(
                "Running the developer engine binary %s (%s)", override, OVERRIDE_ENV
            )
            return override
        arch = engine_arch()
        pin = self._pin
        expected = pin.hash_for(arch)
        if pin.base_url is None or pin.version is None or expected is None:
            raise NoEnginePinned("this release of the integration pins no engine build")
        path = self._bin_dir / f"ha-camerasd-{pin.version}-{arch}"
        if not await self._hass.async_add_executor_job(_matches, path, expected):
            data = await self._async_download(
                engine_url(pin.base_url, pin.version, arch)
            )
            if hashlib.sha256(data).hexdigest() != expected.lower():
                raise VerificationFailed(
                    f"SHA-256 mismatch for engine {pin.version} ({arch})"
                )
            await self._hass.async_add_executor_job(_install, path, data)
        return path

    async def _async_download(self, url: str) -> bytes:
        _LOGGER.info("Downloading the Camera Hub engine from %s", url)
        session = async_get_clientsession(self._hass)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT)
            ) as resp:
                resp.raise_for_status()
                # content.read(n) returns only what has arrived so far; read
                # chunks until the end of the body.
                data = bytearray()
                async for chunk in resp.content.iter_chunked(_CHUNK_BYTES):
                    data += chunk
                    if len(data) > _MAX_BINARY_BYTES:
                        raise EngineBinaryError(f"{url} is too large")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EngineBinaryError(f"download of {url} failed: {err}") from err
        return bytes(data)


def _matches(path: Path, expected: str) -> bool:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return False
    if hashlib.sha256(data).hexdigest() == expected.lower():
        return True
    _LOGGER.warning(
        "Installed engine %s doesn't match its pinned hash; replacing it", path
    )
    path.unlink(missing_ok=True)
    return False


def _install(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.chmod(0o755)
    tmp.replace(path)
