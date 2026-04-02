"""
api/websocket.py — WebSocket connection manager.

Manages all active WebSocket connections and broadcasts messages
to all connected clients. Thread-safe for asyncio use.
"""
import asyncio
import json
import logging
import time
from typing import Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and message broadcasting."""

    def __init__(self):
        self._connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)
        logger.info("WS client connected. Total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)
        logger.info("WS client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, message: dict):
        """Send message to all connected clients. Dead connections are pruned."""
        if not self._connections:
            return

        data = json.dumps(message)
        dead = set()

        for ws in list(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)

        for ws in dead:
            self._connections.discard(ws)

    async def send_to(self, ws: WebSocket, message: dict):
        """Send message to a single client."""
        try:
            await ws.send_text(json.dumps(message))
        except Exception as e:
            logger.warning("Failed to send to client: %s", e)
            self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)


# Singleton instance shared across the app
manager = ConnectionManager()