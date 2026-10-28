"""Tests for obtaining the engine binary."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import re
from unittest import mock

from aiohttp import ClientError
from aiohttp.streams import StreamReader
import pytest
from pytest_homeassistant_custom_component.test_util import aiohttp as aiohttp_mock
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ezlo_camerahub import engine_binary
from custom_components.ezlo_camerahub.engine_binary import (
    OVERRIDE_ENV,
    EngineBinary,
    EngineBinaryError,
    EnginePin,
    NoEnginePinned,
    UnsupportedPlatform,
    VerificationFailed,
    engine_arch,
)
from homeassistant.core import HomeAssistant

DATA = b"\x7fELF fake engine"
SHA = hashlib.sha256(DATA).hexdigest()
URL = "https://example.test/engine/1.2.3/ha-camerasd-x86_64-unknown-linux-musl"


@pytest.fixture(autouse=True)
def amd64_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(engine_binary.sys, "platform", "linux")
    monkeypatch.setattr(engine_binary.platform, "machine", lambda: "x86_64")


def _pin(sha: str = SHA) -> EnginePin:
    return EnginePin(
        base_url="https://example.test/engine",
        version="1.2.3",
        sha256=(("amd64", sha),),
    )


@pytest.mark.parametrize(
    ("machine", "arch"),
    [("x86_64", "amd64"), ("AMD64", "amd64"), ("aarch64", "arm64"), ("arm64", "arm64")],
)
def test_engine_arch(machine: str, arch: str) -> None:
    assert engine_arch(machine, "linux") == arch


@pytest.mark.parametrize(
    ("machine", "system"),
    [("armv7l", "linux"), ("x86_64", "darwin"), ("x86_64", "win32")],
)
def test_engine_arch_unsupported(machine: str, system: str) -> None:
    with pytest.raises(UnsupportedPlatform):
        engine_arch(machine, system)


async def test_download_verify_install(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    aioclient_mock.get(URL, content=DATA)
    path = await EngineBinary(hass, tmp_path, _pin()).async_ensure()
    assert path == tmp_path / "ha-camerasd-1.2.3-amd64"
    assert path.read_bytes() == DATA
    assert path.stat().st_mode & 0o111
    # Cached: no second download.
    assert await EngineBinary(hass, tmp_path, _pin()).async_ensure() == path
    assert aioclient_mock.call_count == 1


async def test_download_reads_the_whole_body(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body arrives over the network in pieces, not in one buffer."""
    data = bytes(range(256)) * 1024
    loop = asyncio.get_running_loop()

    def arriving_in_pieces(body: bytes) -> StreamReader:
        stream = StreamReader(mock.Mock(_reading_paused=False), limit=2**16)
        stream.feed_data(body[:16384])

        def rest() -> None:
            stream.feed_data(body[16384:])
            stream.feed_eof()

        loop.call_soon(rest)
        return stream

    monkeypatch.setattr(aiohttp_mock, "mock_stream", arriving_in_pieces)
    aioclient_mock.get(URL, content=data)
    pin = _pin(hashlib.sha256(data).hexdigest())
    path = await EngineBinary(hass, tmp_path, pin).async_ensure()
    assert path.read_bytes() == data


async def test_tampered_binary_is_replaced(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    aioclient_mock.get(URL, content=DATA)
    path = tmp_path / "ha-camerasd-1.2.3-amd64"
    path.write_bytes(b"tampered")
    assert await EngineBinary(hass, tmp_path, _pin()).async_ensure() == path
    assert path.read_bytes() == DATA


async def test_hash_mismatch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    aioclient_mock.get(URL, content=b"something else")
    with pytest.raises(VerificationFailed):
        await EngineBinary(hass, tmp_path, _pin()).async_ensure()
    assert not (tmp_path / "ha-camerasd-1.2.3-amd64").exists()


async def test_download_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    aioclient_mock.get(URL, exc=ClientError("down"))
    with pytest.raises(EngineBinaryError):
        await EngineBinary(hass, tmp_path, _pin()).async_ensure()


async def test_download_http_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    aioclient_mock.get(URL, status=404)
    with pytest.raises(EngineBinaryError):
        await EngineBinary(hass, tmp_path, _pin()).async_ensure()


async def test_too_large(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_binary, "_MAX_BINARY_BYTES", 4)
    aioclient_mock.get(URL, content=DATA)
    with pytest.raises(EngineBinaryError):
        await EngineBinary(hass, tmp_path, _pin()).async_ensure()


async def test_not_pinned(hass: HomeAssistant, tmp_path: Path) -> None:
    with pytest.raises(NoEnginePinned):
        await EngineBinary(
            hass, tmp_path, EnginePin(base_url=None, version=None, sha256=())
        ).async_ensure()


async def test_override(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "my-engine"
    monkeypatch.setenv(OVERRIDE_ENV, str(local))
    monkeypatch.setattr(engine_binary.sys, "platform", "darwin")
    assert (
        await EngineBinary(hass, tmp_path, EnginePin(None, None, ())).async_ensure()
        == local
    )


def test_engine_url() -> None:
    assert (
        engine_binary.engine_url(
            "https://dl.mios.com/linux_firmware/build/custom_packages/ha-camerasd/standalone",
            "v1.0.0",
            "arm64",
        )
        == "https://dl.mios.com/linux_firmware/build/custom_packages/ha-camerasd/standalone/v1.0.0/ha-camerasd-aarch64-unknown-linux-musl"
    )


def test_default_pin_points_at_dl_mios_com() -> None:
    assert (
        EnginePin().base_url
        == "https://dl.mios.com/linux_firmware/build/custom_packages/ha-camerasd/standalone"
    )


def test_default_pin_is_complete() -> None:
    pin = EnginePin()
    assert pin.version is not None
    for arch in ("amd64", "arm64"):
        assert re.fullmatch(r"[0-9a-f]{64}", pin.hash_for(arch) or "")
