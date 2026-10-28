"""Tests for the engine supervisor, against a fake engine process."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sys

import pytest

from custom_components.ezlo_camerahub import engine
from custom_components.ezlo_camerahub.engine import (
    EngineStartError,
    EngineSupervisor,
    redact,
)
from custom_components.ezlo_camerahub.engine_binary import OVERRIDE_ENV, EngineBinary
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

FAKE = Path(__file__).parent / "fake_engine.py"


@pytest.fixture
def fake_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    wrapper = tmp_path / "ha-camerasd"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv(OVERRIDE_ENV, str(wrapper))
    return wrapper


def _envs(tmp_path: Path) -> list[dict[str, str]]:
    return [
        json.loads(line) for line in (tmp_path / "env.jsonl").read_text().splitlines()
    ]


def _supervisor(
    hass: HomeAssistant, tmp_path: Path, creds=("me@example.com", "s3cret")
) -> EngineSupervisor:
    return EngineSupervisor(
        hass, EngineBinary(hass, tmp_path / "bin"), tmp_path / "cache", lambda: creds
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("url=rtsp://admin:p%40ss@10.0.0.9/s", "url=rtsp://***@10.0.0.9/s"),
        ("RTSPS://u@h/p", "RTSPS://***@h/p"),
        ('{"user_password": "hunter2", "x": 1}', '{"user_password": "***", "x": 1}'),
        ("password=hunter2 next", "password=*** next"),
        ("token: abc.def.ghi", "token: ***"),
        ("nothing secret", "nothing secret"),
    ],
)
def test_redact(line: str, expected: str) -> None:
    assert redact(line) == expected


async def test_start_passes_env_and_stops(
    hass: HomeAssistant,
    fake_binary: Path,
    tmp_path: Path,
    socket_enabled,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        logging.DEBUG, logger="custom_components.ezlo_camerahub.engine.output"
    )
    sup = _supervisor(hass, tmp_path)
    await sup.async_start()
    try:
        env = _envs(tmp_path)[0]
        assert env["HA_CAMERASD_MODE"] == "standalone"
        assert env["HA_CAMERASD_STANDALONE_OEM"] == "501"
        assert env["HA_CAMERASD_STANDALONE_CACHE"] == str(tmp_path / "cache")
        assert env["HA_CAMERASD_STANDALONE_API_ADDR"] == f"127.0.0.1:{sup.port}"
        assert env["HA_CAMERASD_STANDALONE_USER"] == "me@example.com"
        assert env["HA_CAMERASD_STANDALONE_PASSWORD"] == "s3cret"
        assert "HA_CAMERASD_CLOUD_API" not in env
        assert oct((tmp_path / "cache").stat().st_mode & 0o777) == "0o700"
        await asyncio.sleep(0.2)
        assert "rtsp://***@10.0.0.9/stream" in caplog.text
        assert "hunter2" not in caplog.text
    finally:
        await sup.async_stop()
    assert sup._process is None


async def test_no_credentials_once_provisioned(
    hass: HomeAssistant,
    fake_binary: Path,
    tmp_path: Path,
    socket_enabled,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine.CLOUD_API_ENV, "https://api-cloud-stg.ezlo.com")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "provision.json").write_text("{}")
    sup = _supervisor(hass, tmp_path)
    assert sup.provisioned
    await sup.async_start()
    await sup.async_stop()
    env = _envs(tmp_path)[0]
    assert "HA_CAMERASD_STANDALONE_PASSWORD" not in env
    assert "HA_CAMERASD_STANDALONE_USER" not in env
    assert env["HA_CAMERASD_CLOUD_API"] == "https://api-cloud-stg.ezlo.com"


async def test_no_credentials_available(
    hass: HomeAssistant, fake_binary: Path, tmp_path: Path, socket_enabled
) -> None:
    sup = _supervisor(hass, tmp_path, creds=None)
    await sup.async_start()
    await sup.async_stop()
    assert "HA_CAMERASD_STANDALONE_PASSWORD" not in _envs(tmp_path)[0]


async def test_start_fails_when_engine_exits(
    hass: HomeAssistant,
    fake_binary: Path,
    tmp_path: Path,
    socket_enabled,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "fail").touch()
    sup = _supervisor(hass, tmp_path)
    with pytest.raises(EngineStartError):
        await sup.async_start()
    await sup.async_stop()


async def test_start_times_out(
    hass: HomeAssistant, tmp_path: Path, socket_enabled, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeper = tmp_path / "ha-camerasd"
    sleeper.write_text("#!/bin/sh\nexec sleep 30\n")
    sleeper.chmod(0o755)
    monkeypatch.setenv(OVERRIDE_ENV, str(sleeper))
    monkeypatch.setattr(engine, "READY_TIMEOUT", 0.5)
    sup = _supervisor(hass, tmp_path)
    with pytest.raises(EngineStartError):
        await sup.async_start()
    assert sup._process is None


async def test_restart_after_exit_and_issue(
    hass: HomeAssistant,
    fake_binary: Path,
    tmp_path: Path,
    socket_enabled,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "exit_after").write_text("0.3")
    monkeypatch.setattr(engine, "BACKOFF_INITIAL", 0.05)
    monkeypatch.setattr(engine, "FAILURES_BEFORE_ISSUE", 2)
    sup = _supervisor(hass, tmp_path)
    await sup.async_start()
    try:
        for _ in range(100):
            if sup.failures >= 2:
                break
            await asyncio.sleep(0.05)
        assert len(_envs(tmp_path)) >= 2
        assert sup.last_error == "engine exited with code 1"
        assert (
            ir.async_get(hass).async_get_issue("ezlo_camerahub", "engine_crashing")
            is not None
        )
    finally:
        await sup.async_stop()


async def test_restart_failure_counts(
    hass: HomeAssistant,
    fake_binary: Path,
    tmp_path: Path,
    socket_enabled,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "exit_after").write_text("0.3")
    monkeypatch.setattr(engine, "BACKOFF_INITIAL", 0.05)
    sup = _supervisor(hass, tmp_path)
    await sup.async_start()
    (tmp_path / "fail").touch()
    try:
        for _ in range(100):
            if sup.failures >= 3:
                break
            await asyncio.sleep(0.05)
        assert sup.failures >= 3
        assert "exited" in (sup.last_error or "")
    finally:
        await sup.async_stop()


async def test_port_reused_or_replaced(
    hass: HomeAssistant, fake_binary: Path, tmp_path: Path, socket_enabled
) -> None:
    sup = _supervisor(hass, tmp_path)
    busy = __import__("socket").socket()
    busy.bind(("127.0.0.1", sup.port))
    busy.listen()
    taken = sup.port
    try:
        await sup.async_start()
        assert sup.port != taken
    finally:
        busy.close()
        await sup.async_stop()


async def test_orphan_engine_on_same_cache_is_stopped(
    hass: HomeAssistant, fake_binary: Path, tmp_path: Path, socket_enabled
) -> None:
    first = _supervisor(hass, tmp_path)
    await first.async_start()
    orphan = first._process
    assert orphan is not None
    assert first.pid_file.read_text() == str(orphan.pid)
    # Home Assistant "dies": the supervisor is gone but its engine still runs.
    first._task.cancel()
    second = _supervisor(hass, tmp_path)
    await second.async_start()
    try:
        await asyncio.wait_for(orphan.wait(), 15)
        assert orphan.returncode is not None
        assert second.pid_file.read_text() == str(second._process.pid)
    finally:
        await second.async_stop()
    assert not second.pid_file.exists()


def test_stop_orphan_ignores_unrelated_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "engine.pid"
    # Our own process doesn't carry the cache marker: left alone.
    pid_file.write_text(str(__import__("os").getpid()))
    assert engine.stop_orphan(pid_file, tmp_path / "cache") is False
    assert not pid_file.exists()
    pid_file.write_text("999999999")
    assert engine.stop_orphan(pid_file, tmp_path / "cache") is False
    pid_file.write_text("garbage")
    assert engine.stop_orphan(pid_file, tmp_path / "cache") is False
    assert engine.stop_orphan(tmp_path / "missing.pid", tmp_path / "cache") is False
