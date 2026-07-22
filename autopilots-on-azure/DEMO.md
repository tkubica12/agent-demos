# Demonstration guide

This guide is a repeatable classroom path for identity, tools, memory, learning, Dreaming, and Collective Learning Review. Deployment belongs in [DEPLOYMENT.md](DEPLOYMENT.md).

## Before the session

```powershell
Set-Location .\autopilots-on-azure
uv sync

uv run python -m scripts.demo_ops status --runtime openclaw
uv run python -m scripts.demo_ops status --runtime hermes --state-name hermes
uv run python -m scripts.demo_ops status --runtime hermes --state-name hermes2
```

Confirm in Teams:

- the intended human account is in the correct tenant;
- Hermes and Hermes 2 are discoverable;
- a direct chat can be opened with each Worker;
- a channel message uses an explicit Worker mention.

Agent User and Teams license propagation can take 10-15 minutes and sometimes longer.

## Suggested story

1. One platform hosts different Worker runtimes.
2. Each Worker has an autonomous Agent Identity and an Agent User presence.
3. Tools authorize the Worker identity rather than the human caller.
4. One Role Blueprint produces multiple isolated Workers.
5. Each Worker keeps private state and learns locally.
6. Only approved, privacy-safe Learning Packets enter Collective Learning Review.
7. A reviewed Promotion creates a new Role Release.
8. Worker Refresh adopts shared learning without replacing private state.

## 1. Runtime and model proof

Run fresh direct turns:

```powershell
uv run python -m scripts.demo_ops smoke `
  --runtime openclaw `
  --message "Reply with your runtime name, model deployment, and one sentence about your role."

uv run python -m scripts.demo_ops smoke `
  --runtime hermes `
  --state-name hermes `
  --message "Reply with your Worker ID, Role Blueprint, and Role Release."
```

Expected:

- the bridge reaches a real Sandbox;
- the runtime uses the configured Foundry model;
- Hermes reports its Worker and immutable Role Release identity.

## 2. Teams identity

Send a direct Teams message:

```text
Who are you, what Role Blueprint do you use, and what can you do for me?
```

In a channel:

```text
@Hermes summarize what you can help this team coordinate.
```

Point out:

- the Teams presence belongs to the Agent User;
- the Worker runtime authorizes autonomously with Agent Identity;
- a channel mention is targeted delivery, not passive access to all channel traffic.

## 3. Private and public MCP authorization

Private incidents prompt:

```text
Use the incidents tool. List the currently unhealthy services and identify the highest-severity incident.
```

Public shipments prompt:

```text
Use the shipments tool. Find the current state of shipment SHIP-1042.
```

Expected:

- private incidents travel over private networking;
- shipments uses an Entra-protected public HTTPS endpoint;
- each MCP server validates the autonomous Worker token and required app role;
- the human Teams token is not forwarded to MCP.

## 4. Hermes memory and skill model

| Information | Storage | How it is used | Collective review |
| --- | --- | --- | --- |
| Personal Memory | `memories\USER.md`, `memories\MEMORY.md` | Injected into every fresh session | Never |
| Private Playbook | `skills\private\<name>\SKILL.md` | Progressive disclosure or `/name` | Never |
| Work History | Hermes session database | `session_search` and Dreaming evidence | Never raw |
| Role Skill | `skills\role\<name>\SKILL.md` | Progressive disclosure or `/name` | Only a recorded local diff |
| Candidate Improvement | `skills\candidates\<name>\SKILL.md` | Progressive disclosure or `/name` | Artifact plus provenance |
| Learning provenance | `learning\records.jsonl` | Explains why governed behavior changed | Included only for eligible artifacts |

`records.jsonl` is not behavior memory. The effective skill tree contains behavior; the journal supplies evidence, rationale, hashes, Worker identity, and Role Release context.

## 5. Personal Memory

Teach a harmless marker:

```text
Remember as Personal Memory that my harmless marker is LOTUS-81 and I prefer delivery risks summarized as short bullets with an owner and due date. Keep this private to this Worker.
```

Start a fresh Sandbox CLI session and ask:

```text
Without using recall, session_search, or reading files, what is my harmless personal marker and how do I prefer delivery risks summarized?
```

Expected: Hermes answers from the native fresh-session memory injection without a retrieval tool.

## 6. Private Playbook

Create rich assignment-specific knowledge:

```text
Create a private assignment playbook named cedar-delivery. Project Cedar's harmless marker is CEDAR-42. Its weekly status draft is due every Thursday at 15:00 local assignment time. The playbook should tell you to verify owners and due dates. Keep every Cedar-specific detail private and never make it a Candidate Improvement.
```

Test deterministic progressive disclosure in a fresh CLI session:

```text
/cedar-delivery What is Project Cedar's marker and weekly draft deadline?
```

Expected: the answer comes from `skills\private\cedar-delivery`, not Work History.

Personal Memory is for small facts that should always be available. A Private Playbook holds richer private knowledge and procedures that Hermes loads only when relevant.

## 7. Candidate Improvement

Teach Hermes 2 a reusable local capability:

```text
Learn a reusable Candidate Improvement named dependency-handoff-contract: Every dependency handoff must record the provider, receiver, deliverable, acceptance criteria, and needed-by date. Keep it general and attach required provenance.
```

Test it:

```text
/dependency-handoff-contract What must every dependency handoff record?
```

Expected:

- Hermes calls native `skill_manage`;
- `skills\candidates\dependency-handoff-contract\SKILL.md` exists;
- `learning\records.jsonl` gains one schema-v2 record;
- the capability is immediately available to Hermes 2 but not Hermes.

This divergence is intentional. It provides the second independent observation for Collective Learning Review.

## 8. Controlled fresh-session and file inspection

Teams 1:1 follows the Teams conversation identity and has no reliable "new session" button. For deterministic testing, connect to the Worker Sandbox and launch:

```bash
export HERMES_HOME=/data/hermes/profiles/junior-project-manager
cd "$HERMES_HOME/workspace"
hermes --cli
```

Launching `hermes --cli` without `--continue` or `--resume` creates a fresh CLI session. Exit and relaunch between tests.

Inspect canonical state:

```bash
cd "$HERMES_HOME"

echo "=== Personal Memory ==="
sed -n '1,220p' memories/USER.md
sed -n '1,220p' memories/MEMORY.md

echo "=== Private Playbooks ==="
find skills/private -type f -maxdepth 4 -print

echo "=== Role Skills ==="
find skills/role -type f -maxdepth 4 -print

echo "=== Candidate Improvements ==="
find skills/candidates -type f -maxdepth 4 -print

echo "=== Learning provenance ==="
tail -n 10 learning/records.jsonl

echo "=== Quarantine ==="
find learning/quarantine -maxdepth 3 -type f -print
```

This distinguishes canonical memory and skills from apparent recall through Work History.

## 9. Direct CLI reconciliation

Inside `hermes --cli`, create a reusable skill:

```text
/learn a reusable meeting decision record: capture the decision, owner, decision date, rationale, and review trigger. Create it as Candidate Improvement meeting-decision-record.
```

Immediately inspect:

```bash
find "$HERMES_HOME/skills/candidates" -type f -maxdepth 4 -print
tail -n 5 "$HERMES_HOME/learning/records.jsonl"
```

An empty journal at this point is expected. Direct CLI writes do not pass through the bridge transaction.

Reconcile through the bridge:

```powershell
uv run python -m scripts.demo_ops dream `
  --state-name hermes `
  --focus "Reconcile the newest direct-CLI skill observation from learning/quarantine. Preserve private content only as a Private Playbook and attach provenance to any safe Candidate Improvement." `
  --max-records 3
```

Inspect again. Expected:

- unprovenanced drift was quarantined;
- the committed tree was restored;
- safe generalized content was recreated as a Candidate Improvement;
- a provenance record was appended;
- the quarantine record is marked reconciled.

## 10. Dreaming

First create an ordinary Work History observation without foreground learning:

```text
Analyze this delivery problem, but do not create or modify memories, playbooks, Role Skills, or Candidate Improvements during this turn because I want Dreaming to evaluate it later:

Three recent handoffs accepted dates copied from meeting notes. One date used an unstated timezone, another had no accountable confirmer, and the third could not be traced to its source. What pattern do you see and how should future handoffs prevent it?
```

Then run:

```powershell
uv run python -m scripts.demo_ops dream `
  --state-name hermes `
  --focus "Review recent handoff failures for a reusable, privacy-safe delivery procedure." `
  --max-records 1 `
  --timeout 900
```

Expected outcomes:

- a new Candidate Improvement or Role Skill patch plus provenance; or
- no new record because the behavior is already represented; or
- a skipped duplicate.

All three can be correct. Dreaming is retrospective reasoning over Work History, not a requirement to create a skill every time.

## 11. Prepare Learning Packets

Prepare each Worker independently:

```powershell
uv run python -m scripts.collective_learning --state-name hermes prepare
uv run python -m scripts.collective_learning --state-name hermes2 prepare
```

Review each summary. It must exclude Personal Memory, Private Playbooks, and raw Work History.

Approve the exact returned digests:

```powershell
uv run python -m scripts.collective_learning --state-name hermes approve `
  --packet-digest "<hermes-digest>" `
  --approved-by "<operator-alias>"

uv run python -m scripts.collective_learning --state-name hermes2 approve `
  --packet-digest "<hermes2-digest>" `
  --approved-by "<operator-alias>"
```

Export attested packets:

```powershell
New-Item -ItemType Directory -Force .local\collective-learning | Out-Null

uv run python -m scripts.collective_learning --state-name hermes export `
  --output .local\collective-learning\hermes.packet.json

uv run python -m scripts.collective_learning --state-name hermes2 export `
  --output .local\collective-learning\hermes2.packet.json
```

Each export also writes a trusted Worker public-key mapping beside the packet.

## 12. Collective Learning Review

```powershell
uv run python -m scripts.collective_review `
  --packet .local\collective-learning\hermes.packet.json `
  --packet .local\collective-learning\hermes2.packet.json `
  --worker-public-keys .local\collective-learning\hermes.packet.worker-public-keys.json `
  --worker-public-keys .local\collective-learning\hermes2.packet.worker-public-keys.json `
  --next-role-release 3.2.0 `
  --decision-output .local\collective-learning\role-release-3.2.0-decision.json
```

Inspect:

- packet signature and Worker identity validation;
- common support and Worker-specific evidence;
- conflicts and rejected candidates;
- generalized Role Skill proposals;
- privacy decisions and rationale.

Create a draft Promotion pull request only after inspecting the decision:

```powershell
uv run python -m scripts.collective_review `
  --packet .local\collective-learning\hermes.packet.json `
  --packet .local\collective-learning\hermes2.packet.json `
  --worker-public-keys .local\collective-learning\hermes.packet.worker-public-keys.json `
  --worker-public-keys .local\collective-learning\hermes2.packet.worker-public-keys.json `
  --next-role-release 3.2.0 `
  --decision-output .local\collective-learning\role-release-3.2.0-decision.json `
  --create-pr
```

The pull request remains the human Promotion gate. Add `--ready` only when it should immediately enter normal review.

## 13. Agentic Promotion gates

Promotion pull requests automatically trigger five GitHub Agentic Workflows:

| Workflow | Gate or action |
| --- | --- |
| Promotion Triage | Adds Promotion labels and summarizes release impact |
| Promotion Privacy Guardian | Checks for private or assignment-specific leakage |
| Promotion Role Alignment Reviewer | Checks the proposed skill against the Role Blueprint mission |
| Promotion Learning Evidence Auditor | Checks that claims remain within retained Worker evidence |
| Promotion Skill Quality Reviewer | Checks skill structure, progressive disclosure, duplication, and actionable guidance |

The workflow source prompts are under `.github\workflows\promotion-*.md`. Compiled `.lock.yml` files are generated by `gh aw compile` and must not be edited directly.

Run every gate manually against an existing draft Promotion:

```powershell
gh aw run promotion-triage --ref main -F pr_number=5
gh aw run promotion-privacy-review --ref main -F pr_number=5
gh aw run promotion-role-alignment-review --ref main -F pr_number=5
gh aw run promotion-evidence-review --ref main -F pr_number=5
gh aw run promotion-skill-quality-review --ref main -F pr_number=5
```

Inspect workflow status and resulting labels/reviews:

```powershell
gh aw status
gh pr view 5 --json labels,reviews,statusCheckRollup
```

Expected clear-state labels:

```text
promotion
role-release
collective-learning
privacy-clear
role-aligned
evidence-sufficient
skill-quality-clear
```

Any `*-risk`, `role-drift`, or `evidence-weak` label keeps the Promotion in draft for revision. Agentic reviewers use read-only reasoning plus permission-separated safe outputs; they cannot edit the Role Blueprint, mark the PR ready, merge it, or refresh Workers.

## 14. Worker Refresh proof

After the Promotion PR is reviewed and merged:

1. Point both Workers at the newer immutable Role Release.
2. Apply each Worker workspace.
3. Start a fresh session.
4. Verify the promoted Role Skill is present in both Workers.
5. Verify Personal Memory and Private Playbooks remain different.
6. Verify previous-release Candidate Improvements are archived rather than left active.

Refresh must fail before replacement when packet approval, Worker identity, Role Release, signature, or governed-state validation fails.

## What the demo proves

- Worker identity is autonomous and independently authorized.
- Agent User provides Microsoft 365 presence without becoming the runtime credential.
- Private and public MCP paths use explicit Entra resource boundaries.
- Multiple Workers can share one Role Blueprint without sharing private state.
- Hermes native learning changes local behavior promptly.
- Dreaming can discover reusable learning retrospectively.
- Private information never enters the Learning Packet.
- Shared behavior changes only through signed evidence, Collective Learning Review, and human Promotion.
