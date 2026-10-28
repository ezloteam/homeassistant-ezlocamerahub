"""Tests for parsing the engine's JSON into immutable models."""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.ezlo_camerahub.errors import InvalidResponseError
from custom_components.ezlo_camerahub.models import (
    build_hub_data,
    parse_cameras,
    parse_status,
    parse_uploads,
)

from .conftest import load_fixture


def test_parse_status() -> None:
    status = parse_status(load_fixture("status.json"))
    assert status.provisioned is True
    assert status.serial == "105299999"
    assert status.motion_supported is True
    assert status.nma_connected is True
    assert (status.uploads_done, status.uploads_pending, status.uploads_failed) == (
        42,
        1,
        2,
    )
    front = status.cameras["cam_front"]
    assert front.mode == "motion"
    assert front.recording is True
    assert front.motion_capable is True
    assert front.codec == "h264"
    assert status.per_camera_uploads["cam_front"].clips_ok == 40


def test_parse_status_numeric_serial_is_string() -> None:
    raw = load_fixture("status.json")
    raw["hub"]["serial"] = 105299999
    assert parse_status(raw).serial == "105299999"


def test_parse_status_minimal_unprovisioned() -> None:
    status = parse_status({"provisioned": False, "last_error": "boom"})
    assert status.provisioned is False
    assert status.serial == ""
    assert status.last_error == "boom"
    assert status.motion_supported is False
    assert status.cameras == {}


def test_parse_status_absent_motion_capability_is_false() -> None:
    raw = load_fixture("status.json")
    del raw["capabilities"]
    assert parse_status(raw).motion_supported is False


@pytest.mark.parametrize("raw", [None, [], "x", {"cameras": []}, {"hub": "x"}])
def test_parse_status_rejects_garbage(raw) -> None:
    with pytest.raises(InvalidResponseError):
        parse_status(raw)


def test_status_is_immutable() -> None:
    status = parse_status(load_fixture("status.json"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.provisioned = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        status.cameras["new"] = status.cameras["cam_front"]  # type: ignore[index]


def test_parse_cameras() -> None:
    cameras = parse_cameras(load_fixture("cameras.json"))
    assert [c.id for c in cameras] == ["cam_front", "cam_drive"]
    assert cameras[0].name == "Front Door"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"cameras": "x"},
        {"cameras": [{"name": "no id"}]},
        {"cameras": [{"id": 5}]},
    ],
)
def test_parse_cameras_rejects_garbage(raw) -> None:
    with pytest.raises(InvalidResponseError):
        parse_cameras(raw)


def test_camera_repr_never_contains_credentials() -> None:
    cameras = parse_cameras(load_fixture("cameras.json"))
    assert "s3cr" not in repr(cameras[0])
    data = build_hub_data(parse_status(load_fixture("status.json")), cameras, ())
    assert "s3cr" not in repr(data)


def test_parse_uploads() -> None:
    uploads = parse_uploads(load_fixture("uploads.json"))
    assert [u.id for u in uploads] == [103, 102, 101]
    assert uploads[0].source == "motion"
    assert uploads[1].error == "timeout"


def test_parse_uploads_skips_malformed_items() -> None:
    uploads = parse_uploads(
        {"items": [{"id": "nope"}, {"id": 1, "camera_id": "c", "ts": 5}]}
    )
    assert [u.id for u in uploads] == [1]
    assert uploads[0].source is None


def test_parse_uploads_rejects_garbage() -> None:
    with pytest.raises(InvalidResponseError):
        parse_uploads({"items": "x"})


def test_build_hub_data_merges_live_state() -> None:
    data = build_hub_data(
        parse_status(load_fixture("status.json")),
        parse_cameras(load_fixture("cameras.json")),
        parse_uploads(load_fixture("uploads.json")),
    )
    front = data.cameras["cam_front"]
    assert front.name == "Front Door"
    assert front.motion_capable is True
    assert front.rtsp_ok is True
    assert front.clips_ok == 40
    assert front.last_upload_ts == 1761580500
    assert front.last_clip_source == "motion"
    drive = data.cameras["cam_drive"]
    assert drive.codec == "h265"
    assert drive.last_clip_source == "continuous"
    assert drive.last_upload_ts is None


def test_build_hub_data_camera_without_live_state() -> None:
    cameras = parse_cameras(
        {"cameras": [{"id": "cam_new", "name": "New", "rtsp_url": "rtsp://h/p"}]}
    )
    data = build_hub_data(parse_status({"provisioned": True}), cameras, ())
    new = data.cameras["cam_new"]
    assert new.mode == "continuous"
    assert new.recording is False
    assert new.rtsp_ok is False
    assert new.clips_ok == 0
    assert new.last_clip_source is None


@pytest.mark.parametrize(
    ("mode", "recording", "rtsp_ok", "expected"),
    [
        ("continuous", True, True, True),
        ("continuous", True, False, False),
        ("continuous", False, True, False),
        ("motion", True, True, False),
        ("motion", False, True, False),
    ],
)
def test_recording_now(
    mode: str, recording: bool, rtsp_ok: bool, expected: bool
) -> None:
    status = parse_status(
        {
            "provisioned": True,
            "cameras": {
                "c": {"mode": mode, "recording": recording, "rtsp_ok": rtsp_ok}
            },
        }
    )
    cameras = parse_cameras(
        {"cameras": [{"id": "c", "name": "C", "rtsp_url": "rtsp://h/p"}]}
    )
    assert build_hub_data(status, cameras, ()).cameras["c"].recording_now is expected


def test_motion_fields_absent_on_older_engines() -> None:
    status = parse_status(load_fixture("status.json"))
    assert status.cameras["cam_front"].motion_active is None
    assert status.cameras["cam_front"].last_motion_ts is None


def test_motion_fields_parsed() -> None:
    raw = load_fixture("status.json")
    raw["cameras"]["cam_front"].update(motion_active=True, last_motion_ts=1761580700)
    raw["cameras"]["cam_drive"].update(motion_active=False, last_motion_ts=None)
    data = build_hub_data(
        parse_status(raw), parse_cameras(load_fixture("cameras.json")), ()
    )
    assert data.cameras["cam_front"].motion_active is True
    assert data.cameras["cam_front"].last_motion_ts == 1761580700
    assert data.cameras["cam_drive"].motion_active is False
    assert data.cameras["cam_drive"].last_motion_ts is None
