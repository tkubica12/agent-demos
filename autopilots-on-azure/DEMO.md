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
/learn Store as Personal Memory that my harmless marker is LOTUS-81 and I prefer delivery risks summarized as short bullets with an owner and due date. Keep this private to this Worker.
```

Start a fresh Sandbox CLI session and ask:

```text
Without using recall, session_search, or reading files, what is my harmless personal marker and how do I prefer delivery risks summarized?
```

Expected: Hermes answers from the native fresh-session memory injection without a retrieval tool.

## 6. Private Playbook

Create rich assignment-specific knowledge:

```text
/learn Create a private assignment playbook named cedar-delivery. Project Cedar's harmless marker is CEDAR-42. Its weekly status draft is due every Thursday at 15:00 local assignment time. The playbook should tell you to verify owners and due dates. Keep every Cedar-specific detail private and never make it a Candidate Improvement.
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
/learn Create a reusable Candidate Improvement named dependency-handoff-contract: Every dependency handoff must record the provider, receiver, deliverable, acceptance criteria, and needed-by date. Keep it general and attach required provenance.
```

Test it:

```text
/dependency-handoff-contract What must every dependency handoff record?
```

Expected:

- the bridge runs one constrained Hermes invocation in one learning transaction;
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

### Scheduled Dreaming

Run the configured scheduled-learning cycle immediately:

```powershell
uv run python -m scripts.demo_ops scheduled-run --state-name hermes2 --timeout 900
uv run python -m scripts.demo_ops scheduled-status --state-name hermes2
```

Point out that the automated cycle may Dream and prepare a digest, but cannot approve, export, promote, or merge it.

For the production queue-backed scheduler:

```powershell
uv run python -m scripts.servicebus_dream_smoke `
  --state-name hermes2 `
  --due-seconds 180 `
  --timeout 1800
```

The system Dreaming message uses the same managed-identity Service Bus queue and KEDA bridge wake as user schedules. The dedicated ACA scheduled Job no longer exists.

### Repeatable full-lifecycle demonstrations

Use a separate disposable cohort rather than rolling back `hermes` or `hermes2`:

1. Provision two `demo-*` Workers with dedicated Terraform workspaces and Data Disks.
2. Pin both to an immutable baseline Role Release.
3. Teach divergent Candidate Improvements and run the full review pipeline.
4. Use a disposable Promotion branch when demonstrating merge and Worker Refresh.
5. Reset by deleting only demo Sandboxes and Data Disks, then invoke the Workers to recreate the baseline.

Reset automation must refuse any Worker, volume, or workspace not explicitly named `demo-*`.

Dry-run reset:

```powershell
uv run python -m scripts.demo_cohort `
  reset `
  --state-name demo-hermes-a `
  --workspace demo-hermes-a `
  --baseline-release 3.1.0 `
  --baseline-commit 60b8e7ef3fb594f386d5177032df434eb4e62917
```

For a demonstration that includes a real merge, create a disposable base branch:

```powershell
uv run python -m scripts.demo_cohort create-git-base `
  --branch demo/collective-learning-class `
  --baseline-commit 60b8e7ef3fb594f386d5177032df434eb4e62917
```

Target the Promotion at that branch with `--base-branch demo/collective-learning-class`. Delete the demo branch after Worker/Data Disk reset; `main` remains unchanged.

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

## 15. User-scheduled tasks

First run the automated live smoke against Hermes 2:

```powershell
uv run python -m scripts.user_schedule_smoke `
  --state-name hermes2 `
  --due-seconds 180 `
  --timeout 1200
```

The smoke creates a real one-shot Hermes cron job, confirms its next occurrence is armed as a scheduled Service Bus message, observes the bridge at zero replicas before the due time, verifies Service Bus wakes the bridge, checks the durable execution/delivery receipt and exact output hash, confirms the queue is empty with no dead letters, and removes any remaining test job.

For the Teams demonstration, use an existing 1:1 chat or a channel where Hermes 2 is already installed. In a channel, explicitly mention the Worker:

```text
@Hermes 2 Every 3 minutes, reply in this conversation with exactly "Scheduled hello from Hermes 2". Name the task scheduled-hello-demo.
```

Hermes should confirm the schedule. After three minutes, the bridge proactively continues the same Teams conversation and posts the result without another user message.

Manage the canonical Hermes schedule conversationally:

```text
@Hermes 2 List my scheduled tasks.
@Hermes 2 Pause scheduled-hello-demo.
@Hermes 2 Resume scheduled-hello-demo.
@Hermes 2 Run scheduled-hello-demo now.
@Hermes 2 Remove scheduled-hello-demo.
```

Expected:

- only Worker ID, job ID, revision, due time, and message type enter Service Bus; the private prompt remains on the Worker Data Disk;
- one next occurrence is scheduled at a time, including for recurring tasks;
- a locked message remains unsettled until Hermes records execution and proactive delivery succeeds;
- the queue is acknowledged only after Teams returns a concrete activity ID;
- a stale revision or redelivery cannot rerun the Hermes task;
- Teams delivery is at-least-once across the final send/receipt boundary because Teams and Service Bus do not share a transaction;
- channel delivery continues the originating conversation or thread; the bot does not create an arbitrary new channel or conversation;
- a new first-contact personal chat still requires the app to be installed for that user.

## 16. Document-aware work

First run the live Work IQ Word smoke:

```powershell
uv run python -m scripts.document_smoke `
  --state-name hermes2 `
  --timeout 1200
```

The smoke uses the real Hermes 2 Agent User to create a DOCX, read back a unique marker, add a comment, and reply to it. It fails unless every managed Word tool reports success and the result contains the exact marker and a Microsoft 365 SharePoint URL. The temporary document remains in the Hermes 2 Agent User's OneDrive because Work IQ Word does not expose deletion.

Then validate the Teams attachment path in the existing personal chat with Hermes 2:

1. Attach one small `.docx` with recognizable text and at least one Word comment.
2. Send: `Summarize this document and list its comments. Treat the attachment as private turn context and do not learn from it.`
3. Confirm the answer reflects the actual body and comment without asking for another download link.
4. Attach the document with `/learn remember the contents of this attachment` and confirm Hermes explicitly blocks attachment-derived learning.
5. Attach a PDF or another unsupported format and confirm Hermes fails explicitly rather than pretending to read it.

Current limits are 20 `.docx` or UTF-8 `.txt` files, 50 MiB per file, 300 MiB per turn, and 1,000,000 extracted characters. Connector authorization is sent only to the original connector origin; Microsoft sharing URLs use Work IQ Word when direct download is unavailable. Attachment turns restore Personal Memory, Private Playbooks, Role Skills, and Candidate Improvements transactionally.

## 17. Agent User collaboration

Run the proactive Teams smoke:

```powershell
uv run python -m scripts.m365_actions_smoke `
  --state-name hermes2 `
  --recipient admin@tomasonline.net `
  --execute `
  --timeout 1200
```

Hermes 2 creates or reuses a one-to-one chat, identifies itself as the digital Worker, sends a unique project follow-up, and independently reads the message back. The same Work IQ Teams server can return a OneDrive/SharePoint file directly to a user, chat, or channel.

In the Hermes 2 personal chat, paste a shared Word URL and ask:

```text
Append a tracked paragraph saying "The dependency is confirmed." to this shared document. Keep the existing file and formatting, and show me who Microsoft 365 records as the modifier.
```

The thin loopback collaboration MCP downloads, publishes, and cleans up under the Agent User identity. The `office-collaboration` policy skill reuses the pinned MIT `minimax-docx` skill and its Microsoft Open XML CLI for ordinary Word operations and hard validation gates; narrow Open XML adapters cover explicitly tracked changes. It uploads to the same drive item with an ETag guard, then deletes the local copy. Microsoft 365 creates a version attributed to Hermes 2. Ambiguous edits must fail instead of guessing across complex formatting.

To demonstrate lock recovery, keep the shared document open in Word or Teams while asking Hermes to edit it. When Microsoft 365 returns `423 Locked`, Hermes should report that the validated edit is ready and post predefined suggested-action buttons for **Keep trying original** and **Send shared copy now**.

- **Send shared copy now** creates the Agent User copy, grants the invoking user write access, returns the file in Teams, and records a delivery receipt.
- **Keep trying original** schedules deterministic Service Bus retries without another model turn. Close the document and confirm a later proactive message links the updated original. For a forced-expiry test, set the operation deadline in the isolated test fixture and confirm the 24-hour path creates, shares, and delivers a copy exactly once.

A changed original ETag must safely rebase a guarded Word patch or fall back to a copy; it must never overwrite a human edit.

The initial "Document received" message is an Activity Protocol acknowledgement, not completion. The bridge remains available for the bounded Hermes turn through the KEDA cooldown and posts the final result proactively. If system logs show `KEDAScaleTargetDeactivated` before the final response, verify that `user_scheduling_scale_down_seconds` is at least 960 seconds; the supported default is 1200.

Agent 365 configuration keeps one lightweight bridge replica ready. Do not expect the messaging endpoint itself to scale to zero: cold start can exceed the workload response window before acknowledgement. The Hermes Sandbox remains the expensive scale-to-zero boundary.

The Microsoft Open XML gate targets `FileFormatVersions.Microsoft365`; do not use the parameterless `OpenXmlValidator`, which defaults to Office 2007 and rejects valid modern Word attributes. A native session HTTP 5xx does not rerun document tools: the bridge polls the existing transcript for up to 120 seconds and delivers a newly persisted final answer when available.

Existing files are validated twice: once immediately after download and again after editing. Unchanged source defects are reported but allowed; errors introduced by Hermes block publishing. This keeps legacy Word documents usable without silently repairing unrelated content.

The deterministic local proof for encrypted card actions, retry timing, ETag rebase, Service Bus scheduling, persistent-lock retention, copy sharing, 24-hour fallback, receipts, and expiry cleanup is:

```powershell
uv run python -m unittest `
  tests.test_document_cards `
  tests.test_document_operations `
  tests.test_collaboration_mcp `
  tests.test_user_scheduling `
  tests.test_teams_bridge -v
```

Before a manual lock test, validate the deployed transport and card rendering against the existing Hermes 2 personal chat. The command schedules and immediately cancels a future synthetic retry message, then sends one non-actionable card preview:

```powershell
uv run python -m scripts.document_action_smoke `
  --state-name hermes2 `
  --conversation-id "<Teams conversation ID>" `
  --execute
```

The smoke must return a Service Bus sequence number with `cancelled: true` and suggested-action delivery with `accepted: true`. Independently confirm the validation text through a read-only Work IQ Teams message read. The smoke does not create, edit, or retain a document operation.

For Excel, ask Hermes to create a workbook, then write and read a specific range. The tenant-preview Excel MCP provides create/read/comment operations; Agent User Graph workbook operations provide range writes without regenerating the file. Shared PowerPoint slide, note, and comment text can be read, but PowerPoint creation and body editing are not supported.

To validate notifications:

1. Send an email to the Hermes 2 Agent User or mention it in an email.
2. Mention Hermes 2 in a Word, Excel, or PowerPoint comment on a file it can access.
3. Confirm the existing bridge scales from zero, wakes the same Worker, resolves the stable email/comment/document identifiers, and responds through the originating workload.

Notifications reuse `/api/messages`, Agent 365 Activity Protocol authentication, and ACA HTTP scaling. They do not require Graph webhooks, Event Grid, a second endpoint, or Service Bus ingress. Delivery retry guarantees are currently undocumented, so handlers use stable workload IDs and must remain idempotent.

For a Word comment that requests a body edit, the comment thread is the primary review surface. An unlocked document is updated and acknowledged there. If Microsoft 365 blocks publication, Hermes replies with the exact proposed content, concise rationale, explicit not-applied status, and a fresh-mention retry instruction instead of returning only a lock notice.

## 18. Governed Adaptive Cards

In a Hermes 2 personal chat, send:

```text
Use your interactive UI capability to show a confirmation card asking whether I approve the A16 architecture. Do not confirm it yourself.
```

Confirm that Teams renders a native card, the button replaces it with the selected state, and Hermes posts a continuation using only the visible selected label. Reusing the same action must not run a second continuation. Raw model-authored card JSON and action payloads are never delivered.

## 19. Hermes-generated web apps

Ask Hermes 2 to create a small multi-page presentation or dashboard, test it, and publish it for you. The long-running skill sends an early Teams progress update and returns an app ID plus a native `*.adcproxy.io` URL.

Verify that an anonymous request returns `401`, your Entra-authenticated browser opens the app, and reopening it after idle suspension wakes the child Sandbox. The result card must show **Open site**, **Keep 1h / 6h / 24h / 72h**, and **Delete now**. Retention buttons update ACA's native lifecycle policy without a model turn or Service Bus. The current Agent 365 host shows its standard action acknowledgement but does not refresh the original card; ask Hermes for the site list again to see current state.

Ask `What sites do I have running?` and confirm Hermes returns only that authenticated user's live sites in governed cards. A stopped site remains listed and its URL wakes it OnDemand; a deleted site does not appear. Ask Hermes to update and delete an app; updates retain the logical app ID and failed updates preserve the prior working deployment.

Generated apps are ephemeral: one child Sandbox per app, deny-default egress, at most five active apps per Worker, 80 files/2 MiB per artifact, five-minute idle suspend, and native deletion 24 hours after suspension by default. Production promotion requires reviewed source and a standard Container Apps deployment.

## What the demo proves

- Worker identity is autonomous and independently authorized.
- Agent User provides Microsoft 365 presence without becoming the runtime credential.
- Private and public MCP paths use explicit Entra resource boundaries.
- Document content can enter private turn context and managed Word operations without entering durable learning.
- Agent User actions are attributable in Teams, Mail, OneDrive/SharePoint version history, Word comments, and Excel workbooks.
- Native Teams cards remain governed by reviewed rendering and one-time action handling.
- Rich Hermes-authored applications run behind Entra in isolated OnDemand child Sandboxes rather than inside chat or the Worker process.
- Multiple Workers can share one Role Blueprint without sharing private state.
- Ordinary Hermes turns may learn natively, while explicit `/learn` uses one deterministic transactional learning turn without keyword-triggered retries.
- Dreaming can discover reusable learning retrospectively.
- Private information never enters the Learning Packet.
- Shared behavior changes only through signed evidence, Collective Learning Review, and human Promotion.
