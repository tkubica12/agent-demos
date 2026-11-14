from scripts.provision_agent365_instance import (
    DEFAULT_LICENSE_SKUS,
    agent_registration_payload,
    csv_values,
)

assert DEFAULT_LICENSE_SKUS == [
    "AGENT_365",
    "Microsoft_365_E3_(no_Teams)",
    "Microsoft_Teams_Enterprise_New",
]
assert csv_values("A, B,,C") == ["A", "B", "C"]

payload = agent_registration_payload(
    display_name="Step11Support",
    owner_id="owner-id",
    agent_upn="step11support@example.onmicrosoft.com",
    agent_identity_id="identity-id",
    blueprint_id="blueprint-id",
    description="Support agent",
)
assert payload["originatingStore"] == "Microsoft Foundry"
assert payload["agentIdentityBlueprintId"] == "blueprint-id"
assert payload["agentCard"]["skills"][0]["id"] == "support-policy"

print("Agent 365 direct instance payload checks passed.")
