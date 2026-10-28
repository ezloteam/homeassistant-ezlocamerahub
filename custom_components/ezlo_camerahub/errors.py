"""Errors raised talking to the ha-camerasd engine."""

from __future__ import annotations


class CameraHubError(Exception):
    """Base error for the engine control plane."""


class CameraHubConnectionError(CameraHubError):
    """The engine could not be reached."""


class CameraHubResponseError(CameraHubError):
    """The engine answered with an HTTP error."""

    def __init__(self, status: int) -> None:
        """Initialize."""
        super().__init__(f"engine returned HTTP {status}")
        self.status = status


class InvalidResponseError(CameraHubError):
    """The engine answered with something that doesn't match the contract."""
