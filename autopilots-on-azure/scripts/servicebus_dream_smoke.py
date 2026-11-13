from __future__ import annotations

import argparse
import json
import shlex
import time
from typing import Any

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.identity import AzureCliCredential

from scripts.setup_app_tfvars import runtime_outputs_path
from scripts.tf_helpers import PLATFORM_DIR, terraform_output
from scripts.user_schedule_smoke import (
    az_json,
    require_native_gateway,
    runtime_state,
)
from scripts.demo_ops import worker_sandbox


MANAGEMENT_DREAM = r"""
import json,os,pwd,sys
from pathlib import Path
config=json.load(open('/app/.sandbox-service.json'))
environment=config['environment']
os.environ.update({key:str(value) for key,value in environment.items()})
for variable in ('PYTHONPATH','AUTOPILOTS_PLUGIN_PATH'):
 for path in reversed([item for item in environment.get(variable,'').split(os.pathsep) if item]):
  sys.path.insert(0,path)
sys.path.insert(0,environment['AUTOPILOTS_APPLICATION_ROOT'])
account=pwd.getpwnam('hermes')
if os.geteuid()==0:
 os.setgroups([])
 os.setgid(account.pw_gid)
 os.setuid(account.pw_uid)
elif os.geteuid()!=account.pw_uid:
 raise RuntimeError('Dream management requires the native Hermes account.')
home=Path(environment['HERMES_HOME'])
os.environ['HOME']=str(home/'home')
from cron_runtime import cron_diagnostics,enqueue_system_dream_now
diagnostics=cron_diagnostics(home)
def job():
 matches=[item for item in diagnostics['jobs'] if item.get('name')=='Platform Dreaming']
 if len(matches)!=1:
  raise RuntimeError(f'Expected exactly one Platform Dreaming job; found {len(matches)}.')
 return matches[0]
def definition(item):
 result={key:item.get(key) for key in ('id','schedule','enabled','paused','systemType')}
 repeat=item.get('repeat')
 result['repeat']={'times':repeat.get('times')} if isinstance(repeat,dict) else None
 return result
action=sys.argv[1]
if action=='enqueue':
 current=job()
 active=[item for item in diagnostics['systemReceipts']
  if item.get('jobId')==current.get('id') and item.get('state') not in ('completed','failed')]
 if active:
  raise RuntimeError('Platform Dreaming already has an unfinished occurrence.')
 print(json.dumps({'schedule':definition(current),'occurrence':enqueue_system_dream_now(home)}))
elif action=='receipt':
 job_id,revision,occurrence_id=sys.argv[2:5]
 result=next((item for item in cron_diagnostics(home)['systemReceipts']
  if item.get('jobId')==job_id and item.get('revision')==revision
  and item.get('occurrenceId')==occurrence_id),{'state':'pending'})
 if result.get('state')=='completed' and result.get('success') is not True:
  raise RuntimeError(f'Queue-driven Dreaming failed: {result!r}')
 print(json.dumps(result))
elif action=='schedule':
 print(json.dumps({'schedule':definition(job())}))
else:
 raise ValueError('Unsupported management action.')
""".strip()


def system_job(diagnostics: dict[str, Any]) -> dict[str, Any]:
    jobs = [job for job in diagnostics["jobs"] if job.get("name") == "Platform Dreaming"]
    if len(jobs) != 1:
        raise RuntimeError(f"Expected exactly one Platform Dreaming job; found {len(jobs)}.")
    return jobs[0]


def schedule_definition(job: dict[str, Any]) -> dict[str, Any]:
    required = ("id", "schedule")
    if any(not job.get(key) for key in required):
        raise ValueError("Platform Dreaming diagnostics are missing the live schedule.")
    definition = {key: job.get(key) for key in (*required, "enabled", "paused", "systemType")}
    repeat = job.get("repeat")
    if repeat is not None and not isinstance(repeat, dict):
        raise ValueError("Platform Dreaming diagnostics contain an invalid repeat policy.")
    definition["repeat"] = {"times": repeat.get("times")} if repeat is not None else None
    return definition


def completed_receipt(receipt: dict[str, Any]) -> bool:
    if receipt.get("state") == "failed" or (receipt.get("state") == "completed" and receipt.get("success") is not True):
        raise RuntimeError(f"Queue-driven Dreaming failed: {receipt!r}")
    return receipt.get("state") == "completed"


def run_in_runtime(outputs: dict[str, Any], action: str, *arguments: str) -> dict[str, Any]:
    runtime = worker_sandbox(outputs, "runtime")
    command = "/opt/hermes/.venv/bin/python -c " + shlex.quote(MANAGEMENT_DREAM)
    command += " " + " ".join(shlex.quote(value) for value in (action, *arguments))
    with AzureCliCredential(subscription=str(outputs["subscription_id"])) as credential:
        with SandboxGroupClient(
            endpoint_for_region(outputs["sandbox_location"]),
            credential,
            subscription_id=outputs["subscription_id"],
            resource_group=outputs["resource_group_name"],
            sandbox_group=outputs["sandbox_groups"]["runtime"]["name"],
        ) as group:
            sandbox = group.get_sandbox_client(runtime["id"])
            sandbox.ensure_running(timeout=600)
            result = sandbox.exec(command)
    if result.exit_code != 0:
        raise RuntimeError(f"Runtime management command failed: {result.stderr}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("Runtime management command returned a non-object result.")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an ad-hoc queue-driven Dreaming occurrence without modifying its production schedule."
    )
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.timeout < 1:
        raise ValueError("--timeout must be positive.")

    outputs = json.loads(
        runtime_outputs_path("hermes", args.state_name).read_text(
            encoding="utf-8"
        )
    )
    platform = terraform_output(PLATFORM_DIR)
    if not outputs.get("servicebus_dream_enabled"):
        raise RuntimeError("Queue-driven Dreaming is not enabled.")

    resource_group = str(platform["resource_group_name"])
    namespace = str(platform["scheduler_servicebus_namespace_name"])
    queue = str(outputs["scheduler_servicebus_queue_name"])
    if not queue:
        raise RuntimeError("The Worker scheduling queue is not configured.")
    gateway_before = require_native_gateway(outputs)

    def queue_counts() -> dict[str, Any]:
        return az_json(
            "servicebus", "queue", "show", "--resource-group", resource_group,
            "--namespace-name", namespace, "--name", queue, "--query", "countDetails",
        )

    counts_before = queue_counts()

    scheduled = run_in_runtime(outputs, "enqueue")
    schedule_before = scheduled["schedule"]
    enqueued = scheduled["occurrence"]
    job_id = str(enqueued["jobId"])
    revision = str(enqueued["revision"])
    occurrence_id = str(enqueued["occurrenceId"])
    deadline = time.monotonic() + args.timeout
    receipt: dict[str, Any] = {"state": "pending"}
    while time.monotonic() < deadline:
        receipt = run_in_runtime(outputs, "receipt", job_id, revision, occurrence_id)
        if completed_receipt(receipt):
            break
        time.sleep(15)
    else:
        raise TimeoutError(f"Queue-driven Dreaming did not finish; last receipt: {receipt!r}")
    if receipt.get("success") is not True:
        raise RuntimeError(f"Queue-driven Dreaming failed: {receipt!r}")
    result = {
        "ok": True,
        "workerId": outputs["worker_id"],
        "jobId": job_id,
        "gatewayBefore": gateway_before,
        "gatewayAfter": require_native_gateway(outputs),
        "runtimeStateAfter": runtime_state(outputs),
        "receipt": receipt,
    }

    counts_after = queue_counts()
    if int(counts_after.get("deadLetterMessageCount") or 0) > int(counts_before.get("deadLetterMessageCount") or 0):
        raise RuntimeError("A new dead-letter message appeared during the Dreaming smoke.")
    result["queueCounts"] = counts_after
    schedule_after = run_in_runtime(outputs, "schedule")["schedule"]
    if schedule_before != schedule_after:
        raise RuntimeError("The live production Dreaming schedule changed during the smoke; no schedule has been overwritten.")
    result["productionScheduleUnchanged"] = True
    result["productionSchedule"] = schedule_after
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
