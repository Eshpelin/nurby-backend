"""Tests for MediaMTX universal mux.

Covers slug generation and :class:`StreamWorker._resolve_capture_url`
for every stream type. These are pure-function tests. no MediaMTX, no
DB, no ffmpeg. The goal is to lock the URL contract that ingestion +
audio workers depend on.
"""

from __future__ import annotations

import uuid

from services.ingestion.mediamtx_mux import (
    MUX_ELIGIBLE_TYPES,
    PULL_MUX_TYPES,
    mux_rtsp_url,
    mux_slug,
)
from services.ingestion.stream import StreamWorker

CAM_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


# ---- mux_slug ---------------------------------------------------------


def test_rtsp_gets_cam_slug():
    assert mux_slug(CAM_ID, "rtsp") == f"cam-{CAM_ID}"


def test_hls_gets_cam_slug():
    assert mux_slug(CAM_ID, "hls") == f"cam-{CAM_ID}"


def test_usb_with_device_gets_webcam_slug():
    assert mux_slug(CAM_ID, "usb", webcam_device="0") == f"webcam-{CAM_ID}"


def test_usb_without_device_is_not_muxed():
    # Legacy direct-/dev/videoN setups bypass the mux.
    assert mux_slug(CAM_ID, "usb", webcam_device=None) is None


def test_webcam_extracts_slug_from_stream_url():
    url = "rtsp://localhost:8554/macbook-pro-camera-1zpkdt"
    assert mux_slug(CAM_ID, "webcam", stream_url=url) == "macbook-pro-camera-1zpkdt"


def test_webcam_empty_url_is_not_muxed():
    assert mux_slug(CAM_ID, "webcam", stream_url="") is None
    assert mux_slug(CAM_ID, "webcam", stream_url=None) is None


def test_http_mjpeg_is_not_muxed():
    assert mux_slug(CAM_ID, "http_mjpeg") is None


def test_http_snapshot_is_not_muxed():
    assert mux_slug(CAM_ID, "http_snapshot") is None


def test_file_is_not_muxed():
    assert mux_slug(CAM_ID, "file") is None


def test_mux_eligible_set_matches_slug_behavior():
    # Invariant. every eligible type either yields a slug for some valid
    # input, or is explicitly a push type. The complement should all be
    # None regardless of inputs.
    for t in ("http_mjpeg", "http_snapshot", "file"):
        assert t not in MUX_ELIGIBLE_TYPES
    for t in PULL_MUX_TYPES:
        assert t in MUX_ELIGIBLE_TYPES


# ---- mux_rtsp_url -----------------------------------------------------


def test_mux_rtsp_url_uses_settings_base(monkeypatch):
    from shared import config
    monkeypatch.setattr(config.settings, "mediamtx_rtsp_url", "rtsp://mediamtx:8554")
    url = mux_rtsp_url(CAM_ID, "rtsp")
    assert url == f"rtsp://mediamtx:8554/cam-{CAM_ID}"


def test_mux_rtsp_url_trims_trailing_slash(monkeypatch):
    from shared import config
    monkeypatch.setattr(config.settings, "mediamtx_rtsp_url", "rtsp://mediamtx:8554/")
    url = mux_rtsp_url(CAM_ID, "hls")
    assert url == f"rtsp://mediamtx:8554/cam-{CAM_ID}"


def test_mux_rtsp_url_none_for_non_mux_types():
    assert mux_rtsp_url(CAM_ID, "http_mjpeg") is None
    assert mux_rtsp_url(CAM_ID, "http_snapshot") is None
    assert mux_rtsp_url(CAM_ID, "file") is None


# ---- StreamWorker._resolve_capture_url --------------------------------


def _mk(stream_type, **kw):
    kw.setdefault("stream_url", "")
    return StreamWorker(
        camera_id=CAM_ID,
        stream_url=kw["stream_url"],
        recording_enabled=False,
        stream_type=stream_type,
        username=kw.get("username"),
        password=kw.get("password"),
        auth_token=kw.get("auth_token"),
        webcam_device=kw.get("webcam_device"),
    )


def test_resolve_rtsp_goes_through_mux(monkeypatch):
    from shared import config
    monkeypatch.setattr(config.settings, "mediamtx_rtsp_url", "rtsp://mediamtx:8554")
    w = _mk("rtsp", stream_url="rtsp://cam.local:554/stream1",
            username="admin", password="secret")
    # Credentials must not leak through. the mux upstream holds them.
    assert w._resolve_capture_url() == f"rtsp://mediamtx:8554/cam-{CAM_ID}"


def test_resolve_hls_goes_through_mux(monkeypatch):
    from shared import config
    monkeypatch.setattr(config.settings, "mediamtx_rtsp_url", "rtsp://mediamtx:8554")
    w = _mk("hls", stream_url="https://cam.local/stream.m3u8", auth_token="abc")
    assert w._resolve_capture_url() == f"rtsp://mediamtx:8554/cam-{CAM_ID}"


def test_resolve_usb_bridged_goes_through_mux(monkeypatch):
    from shared import config
    monkeypatch.setattr(config.settings, "mediamtx_rtsp_url", "rtsp://mediamtx:8554")
    w = _mk("usb", stream_url="0", webcam_device="0")
    assert w._resolve_capture_url() == f"rtsp://mediamtx:8554/webcam-{CAM_ID}"


def test_resolve_usb_direct_returns_device_index():
    # No webcam_device. legacy direct access to /dev/videoN (or index).
    w = _mk("usb", stream_url="0", webcam_device=None)
    assert w._resolve_capture_url() == 0


def test_resolve_usb_direct_returns_device_path():
    w = _mk("usb", stream_url="/dev/video0", webcam_device=None)
    assert w._resolve_capture_url() == "/dev/video0"


def test_resolve_webcam_goes_through_mux(monkeypatch):
    from shared import config
    monkeypatch.setattr(config.settings, "mediamtx_rtsp_url", "rtsp://mediamtx:8554")
    w = _mk("webcam", stream_url="rtsp://localhost:8554/my-mac-cam")
    assert w._resolve_capture_url() == "rtsp://mediamtx:8554/my-mac-cam"


def test_resolve_http_mjpeg_direct_with_auth():
    w = _mk("http_mjpeg", stream_url="http://cam.local/mjpg",
            username="u", password="p", auth_token="tok")
    url = w._resolve_capture_url()
    assert url.startswith("http://u:p@cam.local/mjpg")
    assert "token=tok" in url


def test_resolve_http_snapshot_direct():
    w = _mk("http_snapshot", stream_url="http://cam.local/snap.jpg")
    assert w._resolve_capture_url() == "http://cam.local/snap.jpg"


def test_resolve_file_returns_path():
    w = _mk("file", stream_url="/tmp/fixture.mp4")
    assert w._resolve_capture_url() == "/tmp/fixture.mp4"
