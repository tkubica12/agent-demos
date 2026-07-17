import tomllib
import os
from pathlib import Path

from microsoft_agents.activity import Activity, ChannelAccount, ConversationAccount, Mention
from starlette.requests import Request

from main import (
    activity_conversation_id,
    activity_message_text,
    activity_user_id,
    user_id_from,
)
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

with Path("agent365.toml").open("rb") as handle:
    publication = build_publish_request(
        tomllib.load(handle),
        agent_guid="00000000-0000-0000-0000-000000000001",
        blueprint_client_id="00000000-0000-0000-0000-000000000002",
        subscription_id="00000000-0000-0000-0000-000000000003",
        agent_name="step-11-agent365-teams",
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
os.environ["AGENT_NAME"] = "step-11-agent365-teams"
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
