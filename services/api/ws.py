import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from shared.auth import decode_access_token

logger = logging.getLogger("nurby.api.ws")

router = APIRouter()

# In-memory set of connected clients
_connections: set[WebSocket] = set()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connections.add(ws)
    try:
        while True:
            # Keep connection alive, handle incoming messages if needed
            data = await ws.receive_text()
            # Echo back for now, will be replaced with proper message handling
            await ws.send_text(json.dumps({"type": "ack", "data": data}))
    except WebSocketDisconnect:
        _connections.discard(ws)


async def broadcast(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    payload = json.dumps(message)
    dead = set()
    for ws in list(_connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    # In-place update. an augmented assignment (-=) would rebind the module
    # global as a function local and raise UnboundLocalError on every call.
    _connections.difference_update(dead)


async def broadcast_person_actions(camera_id, people: list[dict]):
    """Push current per-person actions for a camera (HAR live overlay).

    ``people`` is a list of ``{track_id, person_id?, person_name?, action, confidence}``.
    The HAR ingestion runner calls this on each sampled update. Clients subscribe with
    ``useWSSubscribe("person_actions", handler, cameraId)`` and filter by camera, matching
    the existing transcript_created / vlm_status pattern (single global socket, client-side
    camera filter). Identity is already gated upstream: only person-state tracks carry a
    name; unknown/body tracks are sent without identity or omitted by the runner for
    guardian-facing cameras."""
    await broadcast(
        {"type": "person_actions", "camera_id": str(camera_id), "people": people or []}
    )


# ── Phone-as-mic ────────────────────────────────────────────────────────

# Live browser-mic sessions. one per audio_only camera. Each session
# owns an ffmpeg subprocess that decodes incoming webm/opus chunks
# into a TCP RTSP-like stream that the AudioWorker can av.open().
_mic_sessions: dict[str, "_MicSession"] = {}


class _MicSession:
    """Bridges a browser MediaRecorder stream into a TCP listener that
    the existing AudioWorker can consume.

    Browser publishes opus chunks (webm container) via WebSocket. The
    session writes those bytes to ffmpeg stdin. ffmpeg muxes the
    chunks into a continuous RTP/MPEG-TS stream and serves it over
    TCP on 127.0.0.1:<port>. The camera row's ``stream_url`` is
    expected to be ``tcp://127.0.0.1:<port>`` so the AudioWorker
    pulls from this session.

    The port is derived deterministically from the camera id so a
    reconnect after a tab refresh always hits the same listener.
    """

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self.port = _port_for_camera(camera_id)
        self.process: asyncio.subprocess.Process | None = None
        self._stdin_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        # webm/opus in on stdin, mpegts mux out to a TCP listen socket.
        # AudioWorker av.open("tcp://127.0.0.1:<port>?listen=0") connects
        # to this. listen=1 on ffmpeg makes it the server.
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts",
            "-i", "pipe:0",
            "-acodec", "libopus", "-b:a", "32k",
            "-f", "mpegts",
            f"tcp://127.0.0.1:{self.port}?listen=1",
        ]
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(
                "mic session ffmpeg up camera=%s port=%d pid=%s",
                self.camera_id, self.port, getattr(self.process, "pid", "?"),
            )
        except FileNotFoundError:
            logger.error("ffmpeg not on PATH. browser-mic disabled.")
            self.process = None

    async def write(self, data: bytes) -> bool:
        if self.process is None or self.process.stdin is None:
            return False
        if self.process.returncode is not None:
            return False
        async with self._stdin_lock:
            try:
                self.process.stdin.write(data)
                await self.process.stdin.drain()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False
            except Exception:
                logger.exception("mic write failed camera=%s", self.camera_id)
                return False

    async def stop(self) -> None:
        proc = self.process
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
        self.process = None


def _port_for_camera(camera_id: str) -> int:
    """Deterministic local port in 19000-19999 derived from camera id.
    Avoids collisions with common service ports."""
    h = uuid.UUID(camera_id).int
    return 19000 + (h % 1000)


def mic_stream_url(camera_id: uuid.UUID) -> str:
    """Public helper. cameras that use browser-mic publishing point
    their ``stream_url`` at this. The ingestion AudioWorker av.opens
    the TCP listener.
    """
    return f"tcp://127.0.0.1:{_port_for_camera(str(camera_id))}"


@router.websocket("/ws/mic/{camera_id}")
async def mic_websocket(
    ws: WebSocket,
    camera_id: str,
    token: str = Query(...),
):
    """Browser-mic publisher endpoint.

    Phone visits ``/mic/{camera_id}``, the page captures audio with
    MediaRecorder (webm/opus), and posts each chunk as a binary
    frame here. The session writes them to ffmpeg which serves the
    decoded audio on the deterministic camera-mic TCP port. The
    existing AudioWorker for an audio_only camera with stream_url
    set to that tcp:// URL pulls from there.
    """
    if not decode_access_token(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    session = _mic_sessions.get(camera_id)
    if session is None:
        session = _MicSession(camera_id)
        _mic_sessions[camera_id] = session
    await session.start()
    if session.process is None:
        await ws.send_text(json.dumps({"type": "error", "message": "ffmpeg missing"}))
        await ws.close(code=4500)
        return
    await ws.send_text(json.dumps({"type": "ready", "port": session.port}))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if not data:
                continue
            ok = await session.write(data)
            if not ok:
                await ws.send_text(
                    json.dumps({"type": "error", "message": "encoder closed"})
                )
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("mic ws error camera=%s", camera_id)
    finally:
        # Keep the ffmpeg session alive on disconnect. The next browser
        # reconnect will rejoin the same TCP listener so the audio
        # worker never sees a gap.
        try:
            await ws.close()
        except Exception:
            pass
