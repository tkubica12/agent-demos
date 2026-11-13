from __future__ import annotations

import argparse
import json

from scripts.collective_learning import _operator_request


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair scheduling as the delegated Worker owner.")
    parser.add_argument("--state-name", default="hermes")
    parser.add_argument("--timeout", type=int, default=600)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("recover", help="Sweep stranded final broker attempts without replaying effects.")
    resume = commands.add_parser("resume-known-result", help="Deliver an authenticated stored cron result without inference or queue replay.")
    resume.add_argument("--queue", required=True)
    resume.add_argument("--message-id", required=True)
    resume.add_argument("--execution-id", required=True)
    resume.add_argument("--output-sha256", required=True)
    resume.add_argument("--reason", required=True)
    resolve = commands.add_parser("resolve", help="Resolve a quarantined gateway receipt without replay.")
    resolve.add_argument("--queue", required=True)
    resolve.add_argument("--message-id", required=True)
    resolve.add_argument("--outcome", choices=("completed", "skipped"), required=True)
    resolve.add_argument("--reason", required=True)
    rearm = commands.add_parser("rearm", help="Rearm a verified pre-claim failure with a stable repair identity.")
    rearm.add_argument("--job-id", required=True)
    rearm.add_argument("--revision", required=True)
    rearm.add_argument("--repair-id", required=True)
    rearm.add_argument("--reason", required=True)
    skip = commands.add_parser("skip-native", help="Explicitly skip terminal native work without replay.")
    skip.add_argument("--kind", choices=("cron", "dream"), required=True)
    skip.add_argument("--job-id", required=True)
    skip.add_argument("--revision", required=True)
    skip.add_argument("--occurrence-id")
    skip.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "recover":
        path, body = "recover", {}
    elif args.command == "resume-known-result":
        path, body = "resume-known-result", {
            "queue": args.queue, "messageId": args.message_id, "executionId": args.execution_id,
            "outputSha256": args.output_sha256, "reason": args.reason,
        }
    elif args.command == "resolve":
        path, body = "resolve", {
            "queue": args.queue, "messageId": args.message_id, "outcome": args.outcome, "reason": args.reason,
        }
    elif args.command == "rearm":
        path, body = "rearm-preclaim", {
            "jobId": args.job_id, "revision": args.revision, "repairId": args.repair_id, "reason": args.reason,
        }
    else:
        path, body = "resolve-schedule", {
            "kind": args.kind, "jobId": args.job_id, "revision": args.revision,
            "occurrenceId": args.occurrence_id or args.revision, "reason": args.reason,
        }
    result = _operator_request(
        f"/internal/transport/{path}", method="POST", body=body,
        timeout=args.timeout, state_name=args.state_name,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
