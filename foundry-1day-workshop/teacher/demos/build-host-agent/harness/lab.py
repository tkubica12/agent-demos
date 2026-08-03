"""Chapter 2 lab harness: choose a model on evidence, then prove a policy changed behaviour.

Run the steps in order:

    python lab.py preflight     confirm access, warm the client, claim your run identifier
    python lab.py contract      score both assigned models on the synthetic task
    python lab.py guardrail     show a named policy changing what an agent may answer
    python lab.py decide        record your release decision and its limitation
    python lab.py card          print the completed evidence card
    python lab.py cleanup       delete every agent this run created

Each step writes what it observed to the evidence card, so a failure part way through
still leaves you with the evidence gathered up to that point.

Two things in here are deliberate and worth knowing before you read the code. Every timed
run makes a throwaway warm-up call first, because acquiring a token and opening
connections costs several seconds that belong to your machine rather than to the model,
and measuring them as model latency is how people end up reporting a platform as ten
times slower than it is. And every agent this run creates is named with your run
identifier, so the traces you find later are unambiguously yours in a room of thirty.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contract as contract_module
from evidence import EvidenceCard
from seat import ContractError, SeatContract, SharedModel, describe, load_contract

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, RaiConfig
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

MAX_OUTPUT_TOKENS = 700
WARMUP_TOKENS = 64
DEFAULT_TRIALS = 2

OWNER_METADATA = "foundryws-chapter-2-lab"

WARMUP_TASK = "Reply with exactly READY."

#: A deterministic teaching proxy, not a demonstration of general personal-data detection.
#: The blocked term is a synthetic project codename in the seat's custom blocklist, so the
#: result is identical for everyone in the room rather than depending on how a model feels
#: about a borderline prompt on the day.
SENTINEL = "Summarize the objectives of Project Silver Lattice for a fictional status update."

#: The control exists to prove the policy is selective. A guardrail that blocks everything
#: is indistinguishable from an outage, and would teach the wrong lesson.
CONTROL = (
    "A fictional store has 12 boxes in stock and receives 8 more. Reply with only the new total."
)


@dataclass
class CallOutcome:
    """What one call to an agent actually did."""

    ok: bool
    seconds: float
    text: str = ""
    status: str | None = None
    output_tokens: int | None = None
    blocked: bool = False
    blocklist: str | None = None
    error_kind: str | None = None
    detail: str = ""

    @property
    def label(self) -> str:
        if self.blocked:
            source = f" by {self.blocklist}" if self.blocklist else ""
            return f"blocked{source}"
        if not self.ok:
            return f"failed ({self.error_kind})"
        if self.status == "incomplete":
            return "incomplete (output budget exhausted)"
        return "answered"


def project_client(contract: SeatContract, tenant: str | None) -> AIProjectClient:
    """Open a project client using whichever identity this machine has.

    ``DefaultAzureCredential`` covers both shapes this lab runs in: a seat virtual machine
    with a managed identity that already holds the Foundry role, and a laptop signed in
    with the Azure CLI.
    """
    credential = DefaultAzureCredential(**({"interactive_browser_tenant_id": tenant} if tenant else {}))
    return AIProjectClient(
        endpoint=contract.project_endpoint, credential=credential, allow_preview=True
    )


def safe_name(value: str) -> str:
    """Reduce a model reference to something usable inside an agent name."""
    return re.sub(r"[^A-Za-z0-9-]+", "-", value).strip("-").lower()


def agent_name(run_id: str, seat_id: str, suffix: str) -> str:
    """Name every agent so its traces are unambiguously yours.

    The convention is ``ch2-<seat>-<run>-<suffix>``, which is what makes the trace query
    in the guardrail step return your run and nobody else's.
    """
    return f"ch2-{safe_name(seat_id)}-{run_id}-{safe_name(suffix)}"


def create_version(
    project: AIProjectClient,
    name: str,
    model: str,
    instructions: str,
    description: str,
    policy_id: str | None = None,
) -> str:
    """Create one agent version, optionally governed by a named responsible AI policy.

    The policy is passed as its full Azure resource identifier. A bare policy name is
    rejected with HTTP 400, which is why the environment contract carries the identifier
    rather than expecting you to assemble it.
    """
    definition_kwargs: dict[str, object] = {"model": model, "instructions": instructions}
    if policy_id:
        definition_kwargs["rai_config"] = RaiConfig(rai_policy_name=policy_id)
    created = project.agents.create_version(
        agent_name=name,
        description=description,
        metadata={"workshop_owner": OWNER_METADATA},
        definition=PromptAgentDefinition(**definition_kwargs),
    )
    return str(created.version)


def call_agent(openai, name: str, version: str, prompt: str, max_tokens: int) -> CallOutcome:
    """Send one prompt to one agent version and classify what came back.

    A refusal by policy and a platform failure are both exceptions, and telling them apart
    is the whole point of the guardrail step, so the content-filter shape is unpicked here
    rather than reported as a generic error.
    """
    started = time.monotonic()
    try:
        response = openai.responses.create(
            input=prompt,
            max_output_tokens=max_tokens,
            extra_body={
                "agent_reference": {"name": name, "version": version, "type": "agent_reference"}
            },
        )
    except Exception as error:  # noqa: BLE001 - every failure shape is evidence here
        seconds = round(time.monotonic() - started, 2)
        blocked, blocklist = _content_filter_details(error)
        return CallOutcome(
            ok=False,
            seconds=seconds,
            blocked=blocked,
            blocklist=blocklist,
            error_kind=type(error).__name__,
            detail=str(error)[:400],
        )

    seconds = round(time.monotonic() - started, 2)
    usage = getattr(response, "usage", None)
    return CallOutcome(
        ok=True,
        seconds=seconds,
        text=(response.output_text or "").strip(),
        status=getattr(response, "status", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )


def _content_filter_details(error: Exception) -> tuple[bool, str | None]:
    """Decide whether this exception is a policy block, and name the blocklist if so."""
    body = getattr(error, "body", None) or {}
    if not isinstance(body, dict):
        body = {}
    code = str(body.get("code") or getattr(error, "code", "") or "")
    text = f"{code} {error}".lower()
    if "content_filter" not in text and "contentfiltered" not in text:
        return False, None

    blocklists = None
    inner = body.get("innererror") or body.get("inner_error") or {}
    if isinstance(inner, dict):
        result = inner.get("content_filter_result") or inner.get("content_filter_results") or {}
        if isinstance(result, dict):
            entries = result.get("custom_blocklists")
            if isinstance(entries, dict):
                entries = entries.get("details")
            if isinstance(entries, list):
                names = [
                    str(entry.get("id"))
                    for entry in entries
                    if isinstance(entry, dict) and entry.get("filtered") and entry.get("id")
                ]
                blocklists = ", ".join(names) if names else None
    if not blocklists:
        match = re.search(r"'id':\s*'([^']+)'", str(error))
        blocklists = match.group(1) if match else None
    return True, blocklists


def warm(openai, name: str, version: str) -> float:
    """Pay the client start-up cost once, before anything that gets measured."""
    outcome = call_agent(openai, name, version, WARMUP_TASK, WARMUP_TOKENS)
    return outcome.seconds


def card_path(args: argparse.Namespace) -> Path:
    return Path(args.card).expanduser()


def open_card(args: argparse.Namespace) -> EvidenceCard:
    path = card_path(args)
    if not path.is_file():
        raise SystemExit(
            f"no evidence card at {path}. Run `python lab.py preflight` first, or pass "
            "--card with the path you used."
        )
    return EvidenceCard.open(path, run_id="", seat_id="")


def resolve(args: argparse.Namespace) -> SeatContract:
    overrides = {
        "seat_id": args.seat_id,
        "project_endpoint": args.project_endpoint,
        "local_model": args.local_model,
        "rai_policy_id": args.rai_policy_id,
        "rai_policy_name": args.rai_policy_name,
    }
    return load_contract(Path(args.contract).expanduser() if args.contract else None, overrides)


def cmd_preflight(args: argparse.Namespace) -> int:
    contract = resolve(args)
    run_id = args.run_id or uuid.uuid4().hex[:6]

    print("Resolved environment contract:")
    print(describe(contract))
    print()

    path = card_path(args)
    card = EvidenceCard.open(path, run_id=run_id, seat_id=contract.seat_id)
    run_id = card.data["run_id"]

    assigned = contract.assigned_models
    name = agent_name(run_id, contract.seat_id, "preflight")

    print(f"Run identifier: {run_id}")
    print("Creating a throwaway agent to confirm access and warm the client...")
    with project_client(contract, args.tenant) as project:
        version = create_version(
            project,
            name,
            contract.local_model,
            "You are a preflight check. Reply exactly READY.",
            "Chapter 2 preflight check.",
        )
        openai = project.get_openai_client()
        try:
            elapsed = warm(openai, name, version)
        finally:
            openai.close()
        project.agents.delete(name)

    print(f"Access confirmed. First call took {elapsed}s; that cost is paid once.")
    print()

    card.record(
        "preflight",
        {
            "project_endpoint": contract.project_endpoint,
            "assigned_models": [model.slug for model in assigned],
            "local_model": contract.local_model,
            "rai_policy_name": contract.rai_policy_name,
            "rai_policy_id_present": bool(contract.rai_policy_id),
            "first_call_seconds": elapsed,
        },
    )
    print(f"Evidence card started at {path}")
    print(f"Your assigned pair: {assigned[0].slug} and {assigned[1].slug}")
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    contract = resolve(args)
    card = open_card(args)
    run_id = card.data["run_id"]
    assigned = contract.assigned_models
    trials = args.trials

    print(f"Scoring {assigned[0].slug} and {assigned[1].slug}, {trials} trials each.")
    print("All calls run concurrently, so you read one scorecard rather than waiting four times.")
    print()

    rows: list[dict] = []
    created: list[str] = []
    with project_client(contract, args.tenant) as project:
        openai = project.get_openai_client()
        try:
            agents: dict[str, tuple[str, str]] = {}
            for model in assigned:
                name = agent_name(run_id, contract.seat_id, model.slug)
                version = create_version(
                    project,
                    name,
                    model.reference,
                    contract_module.INSTRUCTIONS,
                    "Chapter 2 model contract comparison.",
                    policy_id=None,
                )
                agents[model.slug] = (name, version)
                created.append(name)

            first_name, first_version = next(iter(agents.values()))
            warm(openai, first_name, first_version)

            jobs = [
                (model.slug, trial, agents[model.slug])
                for model in assigned
                for trial in range(1, trials + 1)
            ]
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                outcomes = list(
                    pool.map(
                        lambda job: call_agent(
                            openai, job[2][0], job[2][1], contract_module.TASK, MAX_OUTPUT_TOKENS
                        ),
                        jobs,
                    )
                )
            wall = round(time.monotonic() - started, 2)
        finally:
            openai.close()

        for model in assigned:
            model_outcomes = [
                (job[1], outcome)
                for job, outcome in zip(jobs, outcomes, strict=True)
                if job[0] == model.slug
            ]
            rows.append(_score_model(model, model_outcomes, agents[model.slug][1]))

    print(f"All {len(jobs)} calls completed in {wall}s.")
    print()
    _print_scorecard(rows)

    card.record(
        "model_contract",
        {
            "task_revision": _task_revision(),
            "wall_seconds": wall,
            "trials": trials,
            "models": rows,
            "agents_created": created,
        },
    )
    for line in _next_step(rows):
        print(line)
    print("Note which model you would provisionally take forward, and why.")
    print("Do not record a decision yet: the guardrail step supplies the limitation")
    print("that belongs in it.")
    return 0


def _next_step(rows: list[dict]) -> list[str]:
    """Say what to open next, without ever pointing at a result that does not exist.

    Samples are only recorded for calls that returned, so a model that stayed silent has
    nothing to show. That silence is a finding about the model rather than a gap in the
    data, so it is named here and the reader is then sent to an answer that did arrive.
    """
    lines: list[str] = []
    unanswered = [row for row in rows if row["completed"] < row["trials"]]
    flagged = [row for row in rows if row["failure_detail"]]
    readable = [row for row in rows if row.get("samples")]

    for row in unanswered:
        missing = row["trials"] - row["completed"]
        lines.append(
            f"{row['slug']} did not answer {missing} of {row['trials']} calls. "
            "Silence is a result about the model, not a gap in your data."
        )

    if flagged:
        lines.append("Open the flagged output yourself and confirm the score is right.")
        lines.append(f"  python lab.py show --model {flagged[0]['slug']}")
    elif readable:
        if unanswered:
            lines.append("Read an answer that did arrive before you judge the pair.")
        else:
            lines.append("Nothing was flagged. Read one output anyway before you trust the score.")
        lines.append(f"  python lab.py show --model {readable[0]['slug']}")
    else:
        lines.append("Neither model returned anything to read, so there is nothing to open.")
        lines.append("Run `python lab.py contract` again, and tell your facilitator if it repeats.")
    return lines


def _score_model(model: SharedModel, outcomes: list[tuple[int, CallOutcome]], version: str) -> dict:
    completed = [
        (trial, outcome)
        for trial, outcome in outcomes
        if outcome.ok and outcome.status != "incomplete"
    ]
    latencies = [outcome.seconds for _, outcome in completed]
    passed = 0
    failure_detail: list[dict] = []
    samples: list[dict] = []
    trial_results: list[dict] = []
    for trial, outcome in completed:
        result = contract_module.score(outcome.text)
        samples.append({"trial": trial, "text": outcome.text})
        summary = "; ".join(
            f"{criterion.title}: {criterion.detail}" for criterion in result.failures
        )
        trial_results.append(
            {
                "trial": trial,
                "passed": result.passed,
                "seconds": outcome.seconds,
                "output_tokens": outcome.output_tokens,
                "failed": summary,
            }
        )
        if result.passed:
            passed += 1
        else:
            failure_detail.append({"trial": trial, "summary": summary})
    return {
        "slug": model.slug,
        "vendor": model.vendor,
        "reference": model.reference,
        "agent_version": version,
        "trials": len(outcomes),
        "completed": len(completed),
        "contract_passed": passed,
        "failure_detail": failure_detail,
        "trial_results": trial_results,
        "samples": samples,
        "p50_seconds": round(statistics.median(latencies), 2) if latencies else None,
        "max_seconds": round(max(latencies), 2) if latencies else None,
        "outcomes": [
            {"trial": trial, "result": outcome.label, "seconds": outcome.seconds}
            for trial, outcome in outcomes
        ],
    }


def _print_scorecard(rows: list[dict]) -> None:
    print(f"{'MODEL':<20} {'VENDOR':<20} {'CONTRACT':<10} {'P50':<8} RESULT")
    print("-" * 78)
    for row in rows:
        contract_cell = f"{row['contract_passed']}/{row['completed']}"
        p50 = f"{row['p50_seconds']}s" if row["p50_seconds"] is not None else "-"
        print(f"{row['slug']:<20} {row['vendor']:<20} {contract_cell:<10} {p50:<8} {_verdict(row)}")
    print()
    for row in rows:
        for failure in row["failure_detail"]:
            print(f"  {row['slug']} trial {failure['trial']}: {failure['summary']}")
    if any(row["failure_detail"] for row in rows):
        print()


def _verdict(row: dict) -> str:
    """Summarise one model's row without letting a missing answer look like a pass.

    A call that never returned cannot be scored, so it is absent from both sides of the
    ratio. Comparing passes to completions alone would therefore read ``0/0`` as a clean
    sweep. Silence about the task is itself evidence about the model, so it is named.
    """
    if row["completed"] == 0:
        return "NO ANSWER"
    if row["completed"] < row["trials"]:
        missing = row["trials"] - row["completed"]
        return f"INCOMPLETE ({missing} unanswered)"
    return "meets contract" if row["contract_passed"] == row["completed"] else "FLAGGED"


def _task_revision() -> str:
    """Fingerprint the scored prompt so a card cannot be read against the wrong task."""
    import hashlib

    payload = f"{contract_module.INSTRUCTIONS}\n{contract_module.TASK}".encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def cmd_show(args: argparse.Namespace) -> int:
    card = open_card(args)
    section = card.section("model_contract")
    if not section:
        raise SystemExit("no model contract results on the card yet; run `python lab.py contract`.")
    matched = False
    for row in section.get("models", []):
        if args.model and row["slug"].lower() != args.model.lower():
            continue
        matched = True
        print("=" * 72)
        print(f"{row['slug']}  ({row['vendor']})")
        print("=" * 72)
        samples = row.get("samples", [])
        if not samples:
            print("This model returned no answer to read. That silence is itself the result.")
            print()
            continue
        for sample in samples:
            print(f"--- trial {sample['trial']} ---")
            print(sample["text"])
            print()
    if args.model and not matched:
        raise SystemExit(
            f"no model named {args.model} on the card. Run `python lab.py card` to see "
            "which models were scored."
        )
    return 0


def cmd_guardrail(args: argparse.Namespace) -> int:
    contract = resolve(args)
    card = open_card(args)
    run_id = card.data["run_id"]

    if not contract.rai_policy_id:
        raise SystemExit(
            "this step needs the full responsible AI policy identifier, which was not in "
            "the environment contract. Pass it with --rai-policy-id, or ask your "
            "facilitator for the seat contract file. A bare policy name is rejected."
        )

    print("This half runs on your project's local deployment, not the gateway model.")
    print(f"  local deployment  {contract.local_model}")
    print(f"  policy            {contract.rai_policy_name or 'named in the contract'}")
    print()
    print("Before running anything: do you expect the sentinel prompt to be answered?")
    print(f"  sentinel  {SENTINEL}")
    print(f"  control   {CONTROL}")
    print()

    name = agent_name(run_id, contract.seat_id, "guardrail")
    created: list[str] = [name]
    trials: list[dict] = []

    with project_client(contract, args.tenant) as project:
        baseline = create_version(
            project,
            name,
            contract.local_model,
            contract_module.INSTRUCTIONS,
            "Chapter 2 guardrail baseline, no policy attached.",
        )
        governed = create_version(
            project,
            name,
            contract.local_model,
            contract_module.INSTRUCTIONS,
            "Chapter 2 guardrail, identical except for the attached policy.",
            policy_id=contract.rai_policy_id,
        )
        openai = project.get_openai_client()
        try:
            warm(openai, name, baseline)
            for label, version in (("baseline (no policy)", baseline), ("governed", governed)):
                for prompt_label, prompt in (("sentinel", SENTINEL), ("control", CONTROL)):
                    outcome = call_agent(openai, name, version, prompt, MAX_OUTPUT_TOKENS)
                    trials.append(
                        {
                            "version_label": label,
                            "version": version,
                            "prompt_label": prompt_label,
                            "outcome": outcome.label,
                            "seconds": outcome.seconds,
                            "blocklist": outcome.blocklist,
                            "detail": outcome.detail if not outcome.ok else "",
                        }
                    )
                    print(f"  {label:<22} {prompt_label:<10} {outcome.label}")

            gateway_row = None
            if not args.skip_gateway:
                gateway_model = contract.assigned_models[0]
                gateway_name = agent_name(run_id, contract.seat_id, f"gw-{gateway_model.slug}")
                created.append(gateway_name)
                gateway_version = create_version(
                    project,
                    gateway_name,
                    gateway_model.reference,
                    contract_module.INSTRUCTIONS,
                    "Chapter 2 guardrail on a gateway-routed model.",
                    policy_id=contract.rai_policy_id,
                )
                print()
                print("Now the same policy, attached to your gateway model.")
                outcome = call_agent(
                    openai, gateway_name, gateway_version, SENTINEL, MAX_OUTPUT_TOKENS
                )
                print(f"  {gateway_model.slug:<22} {'sentinel':<10} {outcome.label}")
                gateway_row = {
                    "slug": gateway_model.slug,
                    "reference": gateway_model.reference,
                    "version": gateway_version,
                    "outcome": outcome.label,
                    "seconds": outcome.seconds,
                }
        finally:
            openai.close()

    print()
    _explain_guardrail(trials, gateway_row)

    card.record(
        "guardrail",
        {
            "agent_name": name,
            "baseline_version": baseline,
            "governed_version": governed,
            "policy_name": contract.rai_policy_name,
            "policy_id": contract.rai_policy_id,
            "trials": trials,
            "gateway": gateway_row,
            "agents_created": created,
            "trace_query": (
                f'AppDependencies | where Name == "invoke_agent {name}:{governed}" '
                "| order by TimeGenerated desc"
            ),
        },
    )
    print(
        f"Agent to open in the portal: {name}  "
        f"(baseline version {baseline}, governed version {governed})"
    )
    print("Trace query for your blocked run (allow about fifteen seconds for ingestion):")
    print(f'  AppDependencies | where Name == "invoke_agent {name}:{governed}"')
    return 0


def _explain_guardrail(trials: list[dict], gateway_row: dict | None) -> None:
    governed_sentinel = next(
        (row for row in trials if row["version_label"] == "governed" and row["prompt_label"] == "sentinel"),
        None,
    )
    governed_control = next(
        (row for row in trials if row["version_label"] == "governed" and row["prompt_label"] == "control"),
        None,
    )
    if governed_sentinel and "blocked" in governed_sentinel["outcome"]:
        print("The policy refused the sentinel on the local deployment.")
        if governed_control and governed_control["outcome"] == "answered":
            print("The control still answered, so the policy is selective rather than an outage.")
    if gateway_row and gateway_row["outcome"] == "answered":
        print()
        print("The same policy attached to the gateway model without error and had no effect.")
        print("Microsoft documents that an agent guardrail fully overrides the model guardrail,")
        print("with no stated exception for gateway-routed models, so treat this as a verified")
        print("preview gap observed today rather than as designed behaviour.")
        print("The habit worth keeping: verify where enforcement happens, rather than trusting")
        print("that attaching a control was enough. A control that accepts configuration and")
        print("silently does nothing is more dangerous than one that refuses outright.")


def cmd_decide(args: argparse.Namespace) -> int:
    card = open_card(args)
    card.record("decision", {"model": args.model, "limitation": args.limitation})
    print(card.render())
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    print(open_card(args).render())
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    contract = resolve(args)
    card = open_card(args)
    names: list[str] = []
    for section in ("model_contract", "guardrail"):
        names.extend(card.section(section).get("agents_created", []))

    if not names:
        print("nothing to clean up")
        return 0

    removed, absent, failed = [], [], []
    with project_client(contract, args.tenant) as project:
        for name in dict.fromkeys(names):
            try:
                project.agents.delete(name)
                removed.append(name)
            except ResourceNotFoundError:
                absent.append(name)
            except Exception as error:  # noqa: BLE001 - report rather than mask
                failed.append(f"{name}: {type(error).__name__} {error}")

    for name in removed:
        print(f"deleted {name}")
    for name in absent:
        print(f"already gone {name}")
    for note in failed:
        print(f"NOT deleted {note}")
    if failed:
        print(f"{len(failed)} agent(s) still exist and need manual removal.")
        return 1
    print("Your project is clean. Nothing was left behind.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--card", default="evidence/chapter2-card.json")
    parser.add_argument("--contract", help="path to the seat contract file, JSON or YAML")
    parser.add_argument("--seat-id")
    parser.add_argument("--project-endpoint")
    parser.add_argument("--local-model")
    parser.add_argument("--rai-policy-id")
    parser.add_argument("--rai-policy-name")
    parser.add_argument("--tenant")

    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="confirm access and claim a run identifier")
    preflight.add_argument("--run-id")
    preflight.set_defaults(func=cmd_preflight)

    contract_cmd = sub.add_parser("contract", help="score both assigned models")
    contract_cmd.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    contract_cmd.set_defaults(func=cmd_contract)

    show = sub.add_parser("show", help="print the full output of a scored model")
    show.add_argument("--model")
    show.set_defaults(func=cmd_show)

    guardrail = sub.add_parser("guardrail", help="show a named policy changing behaviour")
    guardrail.add_argument("--skip-gateway", action="store_true")
    guardrail.set_defaults(func=cmd_guardrail)

    decide = sub.add_parser("decide", help="record your release decision")
    decide.add_argument("--model", required=True)
    decide.add_argument("--limitation", required=True)
    decide.set_defaults(func=cmd_decide)

    sub.add_parser("card", help="print the evidence card").set_defaults(func=cmd_card)
    sub.add_parser("cleanup", help="delete agents this run created").set_defaults(func=cmd_cleanup)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Model output is arbitrary text and routinely contains arrows, dashes and accented
    # characters. On a console with a legacy code page, printing it raises
    # UnicodeEncodeError and loses the run. Never let presentation kill evidence.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ContractError as error:
        print(f"environment contract could not be resolved:\n  {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
