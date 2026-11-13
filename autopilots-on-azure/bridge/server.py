from __future__ import annotations

import asyncio
import socket
from contextlib import ExitStack

import uvicorn

from bridge.transport_ingress import CALLBACK_PORT


async def serve() -> None:
    with ExitStack() as stack:
        sockets = []
        for port in (8000, CALLBACK_PORT):
            listener = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", port))
            listener.listen(2048)
            listener.setblocking(False)
            sockets.append(listener)
        server = uvicorn.Server(uvicorn.Config("bridge.app:app", host="0.0.0.0", port=8000))
        await server.serve(sockets=sockets)


if __name__ == "__main__":
    asyncio.run(serve())
