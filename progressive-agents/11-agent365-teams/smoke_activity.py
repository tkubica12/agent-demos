import tomllib
import os
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from microsoft_agents.activity import Activity, ChannelAccount, ConversationAccount, Mention
from starlette.requests import Request

from main import (
    activity_conversation_id,
    activity_message_text,
    activity_user_id,
    user_id_from,
    optional_agent365_invoke_scope,
)
from microsoft_agents.authentication.msal.msal_auth import MsalAuth
from scripts.agent365_publication import build_publish_request


def sample_activity(text: str) -> Activity:
    recipient = ChannelAccount(id="agent-id", name="Support agent")
    return Activity(
        type="message",
        id="activity-1",
        text=text,
        channel_id="msteams",
        from_property=ChannelAccount(
            id="teams-user",
            aad_object_id="00000000-0000-0000-0000-000000000123",
            name="Ada",
        ),
        recipient=recipient,
        conversation=ConversationAccount(
            id="19:conversation@thread.v2",
            conversation_type="channel",
            tenant_id="00000000-0000-0000-0000-000000000456",
        ),
        entities=[
            Mention(
                mentioned=recipient,
                text="<at>Support agent</at>",
            )
        ],
    )


activity = sample_activity("<at>Support agent</at> what is the return window?")
assert activity_message_text(activity) == "what is the return window?"
assert activity_user_id(activity) == "00000000-0000-0000-0000-000000000123"
assert activity_conversation_id(activity) == "19:conversation@thread.v2"
assert activity.get_agentic_instance_id() is None
assert activity.channel_id == "msteams"
assert "identity_config" in inspect.getsource(MsalAuth.get_agentic_application_token)

raw_mention_activity = SimpleNamespace(
    text="<at>Support agent</at> what is the exchange window?",
    recipient=SimpleNamespace(id="agent-id"),
    get_mentions=lambda: [
        {
            "mentioned": {"id": "agent-id", "name": "Support agent"},
            "text": "<at>Support agent</at>",
        }
    ],
)
assert activity_message_text(raw_mention_activity) == "what is the exchange window?"
with patch(
    "main.InvokeAgentScope.start",
    side_effect=AttributeError("'NonRecordingSpan' object has no attribute 'context'"),
):
    with optional_agent365_invoke_scope(
        request=None,
        scope_details=None,
        agent_details=None,
        caller_details=None,
    ) as unavailable_scope:
        assert unavailable_scope is None

with Path("agent365.toml").open("rb") as handle:
    publication = build_publish_request(
        tomllib.load(handle),
        agent_guid="00000000-0000-0000-0000-000000000001",
        blueprint_client_id="00000000-0000-0000-0000-000000000002",
        subscription_id="00000000-0000-0000-0000-000000000003",
        agent_name="step-11-contoso-support-agent",
        agent_version="3",
    )
assert publication["appVersion"] == "1.0.3"
assert publication["agenticUserTemplate"]["CommunicationProtocol"] == "activityProtocol"

trusted_request = Request(
    {
        "type": "http",
        "method": "POST",
        "path": "/invocations",
        "headers": [(b"x-agent-user-id", b"trusted-user")],
    }
)
assert user_id_from(trusted_request, {"user": {"id": "spoofed-user"}}) == "trusted-user"
previous_agent_name = os.environ.get("AGENT_NAME")
os.environ["AGENT_NAME"] = "step-11-contoso-support-agent"
try:
    untrusted_request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/invocations",
            "headers": [],
        }
    )
    try:
        user_id_from(untrusted_request, {"user": {"id": "spoofed-user"}})
    except ValueError:
        pass
    else:
        raise AssertionError("Hosted invocation accepted an untrusted memory user ID.")
finally:
    if previous_agent_name is None:
        del os.environ["AGENT_NAME"]
    else:
        os.environ["AGENT_NAME"] = previous_agent_name

print("Activity-to-Responses bridge checks passed.")
