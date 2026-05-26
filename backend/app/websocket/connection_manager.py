import asyncio
import logging
from typing import Dict, Any, Optional
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core import config
from app.core.logger import get_logger
from app.utils.time import utc_now_iso

logger = get_logger("websocket.connection_manager")

class ConnectionManager:
    def __init__(self):
        # Maps task_id -> WebSocket connection
        self._active_connections: Dict[str, WebSocket] = {}
        # Maps task_id -> session task details (e.g. heartbeat task, last_activity)
        self._sessions: Dict[str, Dict[str, Any]] = {}
        # Concurrency safety lock
        self._lock = asyncio.Lock()

    async def register(self, task_id: str, websocket: WebSocket, correlation_id: str = "") -> None:
        """
        Registers an active WebSocket connection mapped to task_id.
        """
        async with self._lock:
            self._active_connections[task_id] = websocket
            self._sessions[task_id] = {
                "websocket": websocket,
                "correlation_id": correlation_id,
                "last_activity_time": asyncio.get_event_loop().time(),
                "connected_at": utc_now_iso()
            }
            logger.info(f"WebSocket registered for task_id: {task_id}")

    async def unregister(self, task_id: str) -> None:
        """
        Removes the active WebSocket mapping, but preserves background session parameters
        temporarily for reconnection grace period.
        """
        async with self._lock:
            if task_id in self._active_connections:
                self._active_connections.pop(task_id)
            logger.info(f"WebSocket unregistered (disconnected) for task_id: {task_id}")

    def get_socket(self, task_id: str) -> Optional[WebSocket]:
        """
        Returns the active WebSocket connection for a task_id if open.
        """
        ws = self._active_connections.get(task_id)
        if ws and ws.client_state == WebSocketState.CONNECTED:
            return ws
        return None

    def get_correlation_id(self, task_id: str) -> str:
        session = self._sessions.get(task_id, {})
        return session.get("correlation_id", "")

    async def update_activity(self, task_id: str) -> None:
        """
        Updates the last activity timestamp for heartbeat tracking.
        """
        async with self._lock:
            if task_id in self._sessions:
                self._sessions[task_id]["last_activity_time"] = asyncio.get_event_loop().time()

    async def send_json(self, task_id: str, payload: dict) -> bool:
        """
        Sends a JSON payload to the task's active WebSocket connection if open.
        """
        ws = self.get_socket(task_id)
        if not ws:
            logger.debug(f"Skipping send: task {task_id} has no active WebSocket connection.")
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception as e:
            logger.warning(f"Failed to send JSON to task {task_id}: {e}")
            return False

    async def rebind(self, task_id: str, new_websocket: WebSocket) -> bool:
        """
        Re-binds a new WebSocket connection to an active task session.
        Returns True if rebound successfully, False otherwise.
        """
        async with self._lock:
            if task_id in self._sessions:
                self._active_connections[task_id] = new_websocket
                self._sessions[task_id]["websocket"] = new_websocket
                self._sessions[task_id]["last_activity_time"] = asyncio.get_event_loop().time()
                logger.info(f"Re-bound WebSocket connection for task_id: {task_id}")
                return True
            return False

    async def remove_session(self, task_id: str) -> None:
        """
        Completely cleans up connection and session references.
        """
        async with self._lock:
            if task_id in self._active_connections:
                self._active_connections.pop(task_id)
            if task_id in self._sessions:
                self._sessions.pop(task_id)
            logger.info(f"Session memory cleared for task_id: {task_id}")

    def is_session_active(self, task_id: str) -> bool:
        return task_id in self._sessions

# Singleton instance
connection_manager = ConnectionManager()
