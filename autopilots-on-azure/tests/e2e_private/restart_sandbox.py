"""Stop/resume one authorized Sandbox and require evidence of a real guest reboot."""

import argparse
import json
import uuid


def boot_id(sandbox):
    result = sandbox.exec("cat /proc/sys/kernel/random/boot_id")
    if result.exit_code:
        raise RuntimeError(f"Cannot read Sandbox boot identity: {result.stderr}")
    return str(uuid.UUID(result.stdout.strip()))


def restart(sandbox, timeout):
    before = boot_id(sandbox)
    try:
        sandbox.begin_stop(polling_timeout=timeout).result(timeout=timeout)
    finally:
        sandbox.begin_resume(polling_timeout=timeout).result(timeout=timeout)
    sandbox.wait_for_running(timeout=timeout)
    after = boot_id(sandbox)
    if before == after:
        raise RuntimeError("Stop/resume preserved guest boot state; this is not verified reboot evidence.")
    return {"verifiedReboot": True, "beforeBootId": before, "afterBootId": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--sandbox-id", required=True)
    parser.add_argument("--region", default="swedencentral")
    parser.add_argument("--phase-timeout", type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.phase_timeout <= 120:
        raise ValueError("phase-timeout must be 1-120 seconds; the acceptance caller allows ten minutes.")
    sandbox_id = str(uuid.UUID(args.sandbox_id))
    from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
    from azure.identity import AzureCliCredential

    with AzureCliCredential() as credential, SandboxGroupClient(
        endpoint_for_region(args.region), credential,
        subscription_id=args.subscription, resource_group=args.resource_group, sandbox_group=args.group,
    ) as group, group.get_sandbox_client(sandbox_id) as sandbox:
        evidence = restart(sandbox, args.phase_timeout)
    print(json.dumps({"sandboxId": sandbox_id, **evidence}))


if __name__ == "__main__":
    main()
