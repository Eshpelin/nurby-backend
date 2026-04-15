import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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
    for ws in _connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _connections -= dead
