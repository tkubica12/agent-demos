from azure_cron_provider import AzureCronScheduler


def register(ctx) -> None:
    ctx.register_cron_scheduler(AzureCronScheduler())
