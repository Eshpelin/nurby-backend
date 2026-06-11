"""
MediaMTX universal mux.

Registers a MediaMTX path for every audio-capable camera so that
ingestion (video + audio) never talks to the camera directly. MediaMTX
opens the single upstream session and fans out to all local consumers.

Stream type handling.

- ``rtsp``, ``hls``. Configured as MediaMTX pull sources via the HTTP API
  (``POST /v3/config/paths/add/<slug>`` with ``source: <upstream>``).
  Registered with ``sourceOnDemand`` so the upstream connection is held
  open only while a consumer is subscribed.
- ``usb``. Delegated to the existing :mod:`webcam_bridge` which spawns
  ``ffmpeg`` against the local /dev/videoN or AVFoundation device and
  pushes into MediaMTX.
- ``webcam``. Browser WHIP client pushes into MediaMTX directly. No
  server-side registration needed, the path materializes on first
  publish.
- ``http_mjpeg``, ``http_snapshot``, ``file``. Not muxable, ingestion
  pulls direct. These stream types carry no audio track so the invariant
  "no second RTSP session to the camera" is trivially satisfied.

Runtime config writes (``POST /v3/config/paths/add``) are volatile, so
path registrations survive MediaMTX reloads only while this process
re-runs :meth:`MediaMtxMuxManager.sync`. The camera manager calls
``sync`` on every DB poll, so transient MediaMTX restarts heal within
``POLL_INTERVAL`` seconds.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Iterable

import httpx

from services.ingestion.webcam_bridge import bridge_manager as _usb_bridge
from shared.camera_secrets import unseal
from shared.config import settings
from shared.models import Camera

logger = logging.getLogger("nurby.ingestion.mediamtx_mux")

# Types whose pull URL resolves to a MediaMTX slug instead of the camera
# upstream. usb uses the push bridge, webcam relies on WHIP, rtsp + hls
# use pull sources.
MUX_ELIGIBLE_TYPES: frozenset[str] = frozenset({"rtsp", "hls", "usb", "webcam"})

# Types that get a MediaMTX pull-source registered via the HTTP API. The
# other mux-eligible types use push instead and do not need API writes.
PULL_MUX_TYPES: frozenset[str] = frozenset({"rtsp", "hls"})


def mux_slug(
    camera_id: uuid.UUID,
    stream_type: str,
    stream_url: str | None = None,
    webcam_device: str | None = None,
) -> str | None:
    """Canonical MediaMTX path segment for a camera, or None if the
    camera is not mux-eligible. Pure function, no I/O, safe to call
    from both the manager and StreamWorker.
    """
    if stream_type in PULL_MUX_TYPES:
        return f"cam-{camera_id}"
    if stream_type == "usb":
        # Legacy setups with direct /dev/videoN access do not bridge.
        if not webcam_device:
            return None
        return f"webcam-{camera_id}"
    if stream_type == "webcam":
        # Browser publishes to whatever slug the frontend picked when
        # the camera was created. Stored in stream_url.
        slug = (stream_url or "").rsplit("/", 1)[-1]
        return slug or None
    return None


def mux_rtsp_url(
    camera_id: uuid.UUID,
    stream_type: str,
    stream_url: str | None = None,
    webcam_device: str | None = None,
) -> str | None:
    """Full ``rtsp://mediamtx:8554/<slug>`` URL, or None if not muxed."""
    slug = mux_slug(camera_id, stream_type, stream_url, webcam_device)
    if not slug:
        return None
    base = settings.mediamtx_rtsp_url.rstrip("/")
    return f"{base}/{slug}"


def _build_upstream_source(camera: Camera) -> str | None:
    """Upstream URL MediaMTX should pull from for this camera.

    Credentials are embedded in the URL. MediaMTX does not support
    separate user/pass fields on ``source`` entries.
    """
    if camera.stream_type not in PULL_MUX_TYPES:
        return None
    if not camera.stream_url:
        return None
    # Local import. stream.build_auth_url lives in the stream module to
    # avoid a circular import on package load.
    from services.ingestion.stream import build_auth_url

    url = build_auth_url(camera.stream_url, camera.username, unseal(camera.password))
    if camera.auth_token and camera.stream_type == "hls":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={unseal(camera.auth_token)}"
    return url


class MediaMtxMuxManager:
    """Owns MediaMTX path registration for every mux-eligible camera.

    Composes the existing :class:`WebcamBridgeManager` for USB devices
    and adds HTTP API path registration for RTSP/HLS pull sources.
    """

    def __init__(self) -> None:
        # Reuse the module-level WebcamBridgeManager singleton so the
        # host-side bridge daemon and the in-container ingestion worker
        # cannot race each other with separate supervisor instances.
        self._webcam_bridges = _usb_bridge
        self._registered_pull_slugs: set[str] = set()
        self._lock = asyncio.Lock()

    # ---- public API --------------------------------------------------

    async def ensure(self, camera: Camera) -> None:
        """Ensure the MediaMTX path for this camera is registered.

        Safe to call repeatedly. Idempotent per (camera, config).
        """
        if camera.stream_type == "usb":
            await self._webcam_bridges.ensure(camera)
            return
        if camera.stream_type in PULL_MUX_TYPES:
            await self._ensure_pull_path(camera)
            return
        # webcam. Browser owns the path lifecycle.

    async def remove(self, camera_id: uuid.UUID) -> None:
        """Tear down any MediaMTX path that may exist for this camera.

        Defensive. does not need to know the stream type because the
        possible slug forms are enumerable.
        """
        try:
            await self._webcam_bridges.stop(camera_id)
        except Exception:
            logger.exception("Failed to stop webcam bridge for %s", camera_id)
        for slug in (f"cam-{camera_id}", f"webcam-{camera_id}"):
            try:
                await self._delete_pull_path(slug)
            except Exception:
                logger.exception("Failed to delete MediaMTX path %s", slug)

    async def sync(self, cameras: Iterable[Camera]) -> None:
        """Reconcile MediaMTX state against the desired camera list.

        Called on every DB poll by :class:`CameraManager`. Must be cheap
        enough to run every ``POLL_INTERVAL`` seconds.
        """
        cameras = list(cameras)
        if not settings.disable_webcam_bridge:
            try:
                await self._webcam_bridges.sync(cameras)
            except Exception:
                logger.exception("webcam bridge sync failed")

        desired_pull: dict[str, Camera] = {}
        for cam in cameras:
            if cam.stream_type in PULL_MUX_TYPES:
                slug = mux_slug(cam.id, cam.stream_type, cam.stream_url)
                if slug:
                    desired_pull[slug] = cam

        for cam in desired_pull.values():
            await self._ensure_pull_path(cam)

        stale = self._registered_pull_slugs - set(desired_pull.keys())
        for slug in list(stale):
            await self._delete_pull_path(slug)

    async def stop_all(self) -> None:
        await self._webcam_bridges.stop_all()

    # ---- internals ---------------------------------------------------

    async def _ensure_pull_path(self, camera: Camera) -> None:
        slug = mux_slug(camera.id, camera.stream_type, camera.stream_url)
        if not slug:
            return
        upstream = _build_upstream_source(camera)
        if not upstream:
            logger.warning(
                "Skipping MediaMTX pull path %s. no upstream url on camera",
                slug,
            )
            return

        body = {
            "source": upstream,
            "sourceOnDemand": True,
            "sourceOnDemandStartTimeout": "10s",
            "sourceOnDemandCloseAfter": "10s",
        }
        if camera.stream_type == "rtsp":
            body["rtspTransport"] = "tcp"

        base = settings.mediamtx_api_url.rstrip("/")
        add_url = f"{base}/v3/config/paths/add/{slug}"
        patch_url = f"{base}/v3/config/paths/patch/{slug}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(add_url, json=body)
                if resp.status_code == 400 and "already exists" in resp.text.lower():
                    resp = await client.patch(patch_url, json=body)
                    resp.raise_for_status()
                    logger.debug("Patched MediaMTX pull path %s", slug)
                else:
                    resp.raise_for_status()
                    logger.info("Registered MediaMTX pull path %s", slug)
        except httpx.HTTPError:
            logger.exception("Failed to register MediaMTX path %s", slug)
            return

        async with self._lock:
            self._registered_pull_slugs.add(slug)

    async def _delete_pull_path(self, slug: str) -> None:
        base = settings.mediamtx_api_url.rstrip("/")
        url = f"{base}/v3/config/paths/delete/{slug}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.delete(url)
                if resp.status_code not in (200, 404):
                    resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to delete MediaMTX path %s", slug)
            return
        async with self._lock:
            self._registered_pull_slugs.discard(slug)


# Module-level singleton. The camera manager, camera routes, and the
# host-side bridge daemon all share this instance.
mux_manager = MediaMtxMuxManager()
