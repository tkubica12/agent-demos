"""Cron provider selected by ``cron.provider: azure``; Hermes loads it through provider discovery."""

import os
import sys

for _path in os.environ.get("AUTOPILOTS_PLUGIN_PATH", "/app:/opt/autopilots/hermes-site").split(os.pathsep):
    if _path and _path not in sys.path:
        sys.path.append(_path)

from azure_cron_provider import AzureCronScheduler  # noqa: E402


def register(ctx) -> None:
    ctx.register_cron_scheduler(AzureCronScheduler())
