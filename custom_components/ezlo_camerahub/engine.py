"""Run and supervise the ha-camerasd engine as a child process.

The engine listens on a loopback port picked here, keeps its identity and
cameras in its own cache directory under Home Assistant's storage, and is
restarted with backoff if it exits. The Ezlo login is passed only until the
engine has provisioned (provision.json exists); after that it boots from its
cache and the password is no longer needed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import logging
import os
from pathlib import Path
import re
import signal
import socket
import time
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .engine_binary import EngineBinary, EngineBinaryError

_LOGGER = logging.getLogger(__name__)
_ENGINE_LOGGER = logging.getLogger(f"{__name__}.output")

OEM_EZLO_PROTECT: Final = "501"
CLOUD_API_ENV: Final = "EZLO_CAMERAHUB_CLOUD_API"
READY_TIMEOUT: Final = 30.0
STOP_TIMEOUT: Final = 10.0
BACKOFF_INITIAL: Final = 2.0
BACKOFF_MAX: Final = 60.0
STABLE_AFTER: Final = 60.0
FAILURES_BEFORE_ISSUE: Final = 5
ISSUE_CRASHING: Final = "engine_crashing"

_RTSP_USERINFO = re.compile(r"(rtsps?://)[^/@\s]+@", re.IGNORECASE)
_SECRET_FIELD = re.compile(
    r"((?:password|token|jwt)\"?\s*[=:]\s*\"?)[^\s\",}]+", re.IGNORECASE
)

type Credentials = tuple[str, str]


class EngineStartError(Exception):
    """The engine didn't come up."""


def redact(line: str) -> str:
    """Remove credentials from an engine log line."""
    return _SECRET_FIELD.sub(r"\1***", _RTSP_USERINFO.sub(r"\1***@", line))


def free_port() -> int:
    """Return a loopback TCP port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class EngineSupervisor:
    """Start, watch and stop one engine process."""

    def __init__(
        self,
        hass: HomeAssistant,
        binary: EngineBinary,
        cache_dir: Path,
        credentials: Callable[[], Credentials | None],
    ) -> None:
        """Initialize.

        credentials returns the Ezlo login for a first provision, or None.
        """
        self._hass = hass
        self._binary = binary
        self.cache_dir = cache_dir
        self._credentials = credentials
        self.port = free_port()
        self.failures = 0
        self.last_error: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._output_task: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def pid_file(self) -> Path:
        """Where the running engine's PID is recorded (next to its cache)."""
        return self.cache_dir.parent / "engine.pid"

    @property
    def provisioned(self) -> bool:
        """Whether the engine has a cached identity."""
        return (self.cache_dir / "provision.json").is_file()

    async def async_start(self) -> None:
        """Start the engine and wait until its API answers; then keep it running."""
        await self._start_process()
        self._task = self._hass.async_create_background_task(
            self._supervise(), f"{DOMAIN} engine supervisor"
        )

    async def async_stop(self) -> None:
        """Stop supervising and terminate the engine."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._stop_process()

    async def _supervise(self) -> None:
        backoff = BACKOFF_INITIAL
        while not self._stopping:
            began = time.monotonic()
            assert self._process is not None
            code = await self._process.wait()
            if self._stopping:
                return
            if time.monotonic() - began >= STABLE_AFTER:
                self.failures = 0
                backoff = BACKOFF_INITIAL
            self.failures += 1
            self.last_error = f"engine exited with code {code}"
            self._update_issue()
            while not self._stopping:
                _LOGGER.warning(
                    "Camera Hub engine stopped (%s); restarting in %.0f s",
                    self.last_error,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                try:
                    await self._start_process()
                    break
                except (EngineStartError, EngineBinaryError, OSError) as err:
                    self.failures += 1
                    self.last_error = str(err)
                    self._update_issue()

    def _update_issue(self) -> None:
        if self.failures >= FAILURES_BEFORE_ISSUE:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                ISSUE_CRASHING,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_CRASHING,
                translation_placeholders={"error": self.last_error or "unknown"},
            )

    async def _start_process(self) -> None:
        binary = await self._binary.async_ensure()
        await self._hass.async_add_executor_job(self._prepare_dir)
        # An engine left running by a Home Assistant that was killed must not
        # share the cache with the new one (two engines on one identity).
        await self._hass.async_add_executor_job(
            stop_orphan, self.pid_file, self.cache_dir
        )
        if not await self._hass.async_add_executor_job(_port_free, self.port):
            self.port = free_port()
        self._process = await asyncio.create_subprocess_exec(
            str(binary),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._env(),
        )
        await self._hass.async_add_executor_job(
            self.pid_file.write_text, str(self._process.pid)
        )
        self._output_task = self._hass.async_create_background_task(
            self._forward_output(self._process), f"{DOMAIN} engine output"
        )
        try:
            await self._wait_ready()
        except BaseException:
            await self._stop_process()
            raise
        if self.failures:
            _LOGGER.info("Camera Hub engine is running again")
        ir.async_delete_issue(self._hass, DOMAIN, ISSUE_CRASHING)

    def _env(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HA_CAMERASD_MODE": "standalone",
            "HA_CAMERASD_STANDALONE_OEM": OEM_EZLO_PROTECT,
            "HA_CAMERASD_STANDALONE_CACHE": str(self.cache_dir),
            "HA_CAMERASD_STANDALONE_API_ADDR": f"127.0.0.1:{self.port}",
            "HA_CAMERASD_LOG": "info",
        }
        if cloud_api := os.environ.get(CLOUD_API_ENV):
            env["HA_CAMERASD_CLOUD_API"] = cloud_api
        if not self.provisioned and (credentials := self._credentials()) is not None:
            (
                env["HA_CAMERASD_STANDALONE_USER"],
                env["HA_CAMERASD_STANDALONE_PASSWORD"],
            ) = credentials
        return env

    async def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT
        while True:
            process = self._process
            if process is None or process.returncode is not None:
                raise EngineStartError(
                    f"engine exited with code {process.returncode if process else None}"
                )
            try:
                _reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            except OSError:
                if time.monotonic() > deadline:
                    raise EngineStartError("engine API did not come up") from None
                await asyncio.sleep(0.2)
                continue
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return

    async def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), STOP_TIMEOUT)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if self._output_task is not None:
            self._output_task.cancel()
            self._output_task = None
        if process is not None:
            await self._hass.async_add_executor_job(self.pid_file.unlink, True)

    def _prepare_dir(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache_dir.chmod(0o700)

    @staticmethod
    async def _forward_output(process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            _ENGINE_LOGGER.debug("%s", redact(line.decode(errors="replace").rstrip()))


def stop_orphan(pid_file: Path, cache_dir: Path) -> bool:
    """Terminate the recorded engine if it is still running on this cache.

    The process must carry HA_CAMERASD_STANDALONE_CACHE=<cache_dir> in its
    environment; only that one variable is compared, nothing is logged from
    it. Returns True if a process was stopped.
    """
    try:
        pid = int(pid_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return False
    marker = f"HA_CAMERASD_STANDALONE_CACHE={cache_dir}".encode()
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        pid_file.unlink(missing_ok=True)
        return False
    if marker not in environ:
        pid_file.unlink(missing_ok=True)
        return False
    _LOGGER.warning(
        "Stopping a Camera Hub engine left running from before (pid %s)", pid
    )
    for sig, wait in ((signal.SIGTERM, STOP_TIMEOUT), (signal.SIGKILL, 2.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and Path(f"/proc/{pid}").exists():
            time.sleep(0.1)
        if not Path(f"/proc/{pid}").exists():
            break
    pid_file.unlink(missing_ok=True)
    return True


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True
