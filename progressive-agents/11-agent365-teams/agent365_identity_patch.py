from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
PATCH_FLAG = "_step11_foundry_fmi_patch"


async def get_agentic_application_token_with_fmi(
    self,
    tenant_id: str,
    agent_app_instance_id: str,
) -> str | None:
    from azure.identity.aio import DefaultAzureCredential
    from microsoft_agents.authentication.msal.errors import authentication_errors

    if not agent_app_instance_id:
        raise ValueError(
            str(authentication_errors.AgentApplicationInstanceIdRequired)
        )

    credential_options: dict[str, Any] = {
        "identity_config": {"fmi_path": agent_app_instance_id},
    }
    blueprint_client_id = getattr(self._msal_configuration, "CLIENT_ID", None)
    if blueprint_client_id:
        credential_options["managed_identity_client_id"] = blueprint_client_id

    credential = DefaultAzureCredential(**credential_options)
    try:
        token = await credential.get_token("api://AzureAdTokenExchange/.default")
        return token.token
    except Exception:
        logger.exception(
            "Foundry FMI token acquisition failed for agent instance %s",
            agent_app_instance_id,
        )
        return None
    finally:
        await credential.close()


def apply_foundry_fmi_patch() -> None:
    from microsoft_agents.authentication.msal.msal_auth import MsalAuth

    if getattr(MsalAuth, PATCH_FLAG, False):
        return
    MsalAuth.get_agentic_application_token = get_agentic_application_token_with_fmi
    setattr(MsalAuth, PATCH_FLAG, True)
    logger.info("Applied Foundry fmi_path patch to MsalAuth.")
