import asyncio
import time
from dataclasses import dataclass, field

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from .auth import Identity
from .domain import can_read


@dataclass(eq=False)
class Connection:
    socket: WebSocket
    user_id: str
    identity: Identity
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))


class Hub:
    def __init__(self, sessions):
        self.sessions = sessions
        self.connections: set[Connection] = set()

    def publish(self, event, user_ids=None):
        for connection in tuple(self.connections):
            if user_ids is not None and connection.user_id not in user_ids:
                continue
            if "conversationId" in event:
                with self.sessions() as session:
                    if not can_read(session, event["conversationId"], connection.user_id):
                        continue
            try:
                connection.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow peers are disconnected rather than blocking all senders.
                self.connections.discard(connection)
                asyncio.create_task(connection.socket.close(code=1013))

    async def writer(self, connection):
        try:
            while True:
                remaining = connection.identity.expires_at - time.time()
                if remaining <= 0:
                    await connection.socket.close(code=1008, reason="Access token expired")
                    return
                try:
                    event = await asyncio.wait_for(connection.queue.get(), timeout=min(remaining, 30))
                except asyncio.TimeoutError:
                    continue
                if connection.identity.expires_at <= time.time():
                    await connection.socket.close(code=1008, reason="Access token expired")
                    return
                # Revalidate authorization at delivery, not only at subscription/queue time.
                if "conversationId" in event:
                    with self.sessions() as session:
                        if not can_read(session, event["conversationId"], connection.user_id):
                            continue
                await asyncio.wait_for(connection.socket.send_json(event), timeout=2)
        except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError, OSError):
            self.connections.discard(connection)
