"""The evidence card: what you can show someone who was not in the room.

A release decision that lives only in your memory is not evidence. This module keeps a
single JSON file per run that accumulates what each step observed, so that at the end you
have a record with model identity, contract results, latency, agent versions, the policy
identifier and your own stated decision and limitation.

It is deliberately append-only in spirit. Each step writes its own section and nothing
rewrites an earlier one, because a card that quietly changes under you cannot be used to
argue anything later.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CARD_VERSION = 1


@dataclass
class EvidenceCard:
    """One run's accumulated evidence, backed by a JSON file on disk."""

    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, path: Path, run_id: str, seat_id: str) -> EvidenceCard:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = {
                "card_version": CARD_VERSION,
                "run_id": run_id,
                "seat_id": seat_id,
                "started_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "host": platform.node(),
                "sections": {},
            }
        card = cls(path=path, data=data)
        card.save()
        return card

    def record(self, section: str, payload: dict[str, Any]) -> None:
        """Store one step's findings under its own key and flush to disk immediately.

        Flushing on every write matters more than it sounds: if a later step fails, the
        earlier evidence is already durable and the run is still worth something.
        """
        self.data.setdefault("sections", {})[section] = {
            "recorded_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            **payload,
        }
        self.save()

    def section(self, name: str) -> dict[str, Any]:
        return self.data.get("sections", {}).get(name, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def render(self) -> str:
        """Render the card as text you can read aloud or paste into a decision record."""
        sections = self.data.get("sections", {})
        lines = [
            "=" * 72,
            f"EVIDENCE CARD  run {self.data.get('run_id')}  seat {self.data.get('seat_id')}",
            "=" * 72,
        ]

        preflight = sections.get("preflight")
        if preflight:
            lines += [
                "",
                "Environment",
                f"  project        {preflight.get('project_endpoint')}",
                f"  assigned pair  {', '.join(preflight.get('assigned_models', []))}",
                f"  local model    {preflight.get('local_model')}",
            ]

        contract = sections.get("model_contract")
        if contract:
            lines += ["", "Model contract", f"  task revision  {contract.get('task_revision')}"]
            for row in contract.get("models", []):
                lines.append(
                    f"  {row['slug']:<20} {row['vendor']:<18} "
                    f"contract {row['contract_passed']}/{row['completed']} scored  "
                    f"p50 {row['p50_seconds']}s"
                )
                lines.append(f"      {row['reference']}  agent version {row['agent_version']}")
                incomplete = row["trials"] - row["completed"]
                if incomplete:
                    lines.append(
                        f"      {incomplete} of {row['trials']} call(s) never returned a "
                        "scorable answer and are excluded from the score"
                    )
                for trial in row.get("trial_results", []):
                    verdict = "pass" if trial["passed"] else "FAIL"
                    tokens = trial.get("output_tokens")
                    tokens_cell = f"{tokens} output tokens" if tokens is not None else "tokens n/a"
                    lines.append(
                        f"      trial {trial['trial']}: {verdict}  "
                        f"{trial['seconds']}s  {tokens_cell}"
                    )
                    if not trial["passed"]:
                        lines.append(f"        {trial['failed']}")
            if contract.get("selected"):
                lines.append(f"  provisional candidate  {contract['selected']}")

        guardrail = sections.get("guardrail")
        if guardrail:
            lines += [
                "",
                "Guardrail as a versioned control",
                f"  agent              {guardrail.get('agent_name')}",
                f"  baseline version   {guardrail.get('baseline_version')} (no policy)",
                f"  governed version   {guardrail.get('governed_version')} "
                f"(policy {guardrail.get('policy_name')})",
                f"  policy identifier  {guardrail.get('policy_id', 'not recorded')}",
            ]
            for row in guardrail.get("trials", []):
                lines.append(
                    f"  {row['version_label']:<20} {row['prompt_label']:<18} {row['outcome']}"
                )
            if guardrail.get("gateway"):
                gateway = guardrail["gateway"]
                lines += [
                    "",
                    "  Same policy on the gateway model",
                    f"    model            {gateway.get('slug')}",
                    f"    version          {gateway.get('version')}",
                    f"    sentinel outcome {gateway.get('outcome')}",
                ]

        decision = sections.get("decision")
        if decision:
            lines += [
                "",
                "Release decision",
                f"  model taken forward  {decision.get('model')}",
                f"  limitation           {decision.get('limitation')}",
            ]

        lines += ["", f"card written to {self.path}", ""]
        return "\n".join(lines)
