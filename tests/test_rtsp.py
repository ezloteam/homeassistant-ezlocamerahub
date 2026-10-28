"""Tests for RTSP URL credential handling."""

from __future__ import annotations

import pytest

from custom_components.ezlo_camerahub.rtsp import redact_rtsp_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "rtsp://admin:s3cr%40t@10.0.9.64:554/Preview_01_sub",
            "rtsp://***@10.0.9.64:554/Preview_01_sub",
        ),
        ("rtsp://admin@cam/stream", "rtsp://***@cam/stream"),
        (
            "rtsp://10.0.9.65:554/Preview_01_main",
            "rtsp://10.0.9.65:554/Preview_01_main",
        ),
        ("rtsp://u:p@ss:w@rd@host/path?x=1", "rtsp://***@host/path?x=1"),
        ("", ""),
        ("not a url", "not a url"),
    ],
)
def test_redact_rtsp_url(url: str, expected: str) -> None:
    assert redact_rtsp_url(url) == expected
