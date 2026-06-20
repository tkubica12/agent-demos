from __future__ import annotations

import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles


WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


async def index(_: Request) -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app = Starlette(
    routes=[
        Route("/", index),
        Mount("/web", StaticFiles(directory=WEB_DIR), name="web"),
    ]
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8095)
