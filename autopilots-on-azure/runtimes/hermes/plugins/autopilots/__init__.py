"""Autopilots on Azure extensions loaded by the native Hermes plugin system."""

import os
import sys
from pathlib import Path

for _path in os.environ.get("AUTOPILOTS_PLUGIN_PATH", "/app:/opt/autopilots/hermes-site").split(os.pathsep):
    if _path and _path not in sys.path:
        sys.path.append(_path)


def register(ctx) -> None:
    import native_learning
    import runtime_keepalive
    import cron_origin
    from bridge import hermes_telemetry
    from runtime_routes import register_routes

    hermes_telemetry.register(ctx)
    runtime_keepalive.register(ctx)
    cron_origin.register(ctx)
    native_learning.register_attachment_privacy(ctx)
    profile_home = Path(os.environ["HERMES_HOME"])
    ctx.register_platform_handler(
        "api_server", lambda native, adapter: register_routes(native, profile_home, adapter=adapter),
    )
