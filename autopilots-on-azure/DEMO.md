# Hermes demonstration

Use existing `hermes` and `hermes2` Workers after [deployment validation](DEPLOYMENT.md). They share a role but not identities or Data Disks. Run personal-memory examples in **1:1 chats**, not shared groups. Use harmless synthetic facts throughout.

Commands run from `autopilots-on-azure`. [SPEC.md](SPEC.md) defines boundaries and current limitations.

## 1. Inference and tools

```powershell
uv run python -m scripts.demo_ops status --state-name hermes2
uv run python -m scripts.demo_ops smoke --state-name hermes2 `
  --message "Reply exactly: Hermes ready" --timeout 600
```

In Teams, send a personal message or explicitly mention the Worker:

```text
@Hermes 2 Use private incidents to list services and their health.
Then use public shipments to list demo shipments. Identify your sources.
```

Expect real MCP results: incidents through private ingress, shipments through public HTTPS, both authorized as Agent Identity. Foundry inference uses the runtime's managed identity; the human token is not forwarded to tools.

## 2. Memory versus skills

In a personal chat:

```text
/learn Remember my harmless marker LOTUS-81 and my preference for delivery-risk summaries with an owner and due date. Store these as Personal Memory, not a reusable skill.
```

Then send `/new`, followed by:

```text
Without retrieval tools or file reads, what is my marker and summary preference?
```

Expect fresh-session memory injection. `/new` changes the transcript, not durable memory.

Create a richer private procedure:

```text
/learn Create Private Playbook cedar-delivery. Project Cedar's harmless marker is CEDAR-42. Its weekly draft is due Thursday at 15:00 Europe/Prague. Verify owners and deadlines. Keep all Cedar-specific details private.
```

After `/new`:

```text
/cedar-delivery What is the marker and weekly draft deadline?
```

Expect progressive skill loading rather than mandatory injection into every prompt.

| Inspect on the Worker profile | Meaning |
| --- | --- |
| `memories\USER.md`, `memories\MEMORY.md` | Personal Memory |
| `skills\private\cedar-delivery\SKILL.md` | Private Playbook |
| `state.db` and session data | Work History |
| `skills\role\` | Inherited Role Skills |
| `skills\candidates\` | Local reusable additions |
| `learning\records.jsonl` | Why governed skills changed, not behavior memory |

## 3. Local reusable learning

On Hermes 2:

```text
/learn Create Candidate Improvement dependency-handoff-contract. A dependency handoff must record provider, receiver, deliverable, acceptance criteria, and needed-by date. Keep it general, use one SKILL.md only, and attach required provenance with synthetic proposed test cases.
```

After `/new`:

```text
/dependency-handoff-contract What must every dependency handoff record?
```

Expect one constrained transactional learning turn, a candidate skill, and provenance 3.0. The change is local to Hermes 2. A failed privacy/provenance check rolls back governed state rather than reporting persistence.

For multi-Worker review, independently teach Hermes a related generalized correction, such as requiring explicit confirmation of a handoff date and its timezone. Do not copy private Cedar details or manufacture agreement between Workers.

Direct native `hermes --cli` edits bypass the bridge transaction. The next bridged turn/Dream quarantines and reconciles unprovenanced drift; do not treat an immediate CLI file write as export-ready learning.

## 4. Dreaming

Create an ordinary observation:

```text
Analyze this pattern without changing memory or skills: three handoffs accepted dates copied from notes; one lacked a timezone, another an accountable confirmer, and the third a traceable source. What should future handoffs verify?
```

Then:

```powershell
uv run python -m scripts.demo_ops dream --state-name hermes2 `
  --focus "Review recent handoff failures for a reusable, privacy-safe procedure." `
  --max-records 1 --timeout 900
```

Expect a private memory/playbook change, a governed create/patch with provenance, or no change when already represented. Dreaming is not required to invent a skill.

Run an ad-hoc queue-backed Dream without changing production cron:

```powershell
uv run python -m scripts.servicebus_dream_smoke `
  --state-name hermes2 --timeout 1800
```

Expect fenced phase/receipt completion and optional packet preparation. A successful no-change result can report `recordCount=0`, `packet=null`. It cannot approve/export learning.

## 5. Prepare, review, approve, export

Prepare both Workers independently:

```powershell
uv run python -m scripts.collective_learning --state-name hermes prepare
uv run python -m scripts.collective_learning --state-name hermes2 prepare
```

Review the returned summaries, generalized evidence, and proposed scenarios. Packet 2.0 must contain only allowed skill changes and cumulative provenance—not Personal Memory, Private Playbooks, documents, or raw Work History.

Approve each exact returned digest:

```powershell
$hermesDigest = Read-Host "Reviewed Hermes packet digest"
$hermes2Digest = Read-Host "Reviewed Hermes 2 packet digest"
$operator = Read-Host "Approving operator alias"
uv run python -m scripts.collective_learning --state-name hermes approve `
  --packet-digest $hermesDigest --approved-by $operator
uv run python -m scripts.collective_learning --state-name hermes2 approve `
  --packet-digest $hermes2Digest --approved-by $operator

New-Item -ItemType Directory -Force .local\collective-learning | Out-Null
uv run python -m scripts.collective_learning --state-name hermes export `
  --output .local\collective-learning\hermes.packet.json
uv run python -m scripts.collective_learning --state-name hermes2 export `
  --output .local\collective-learning\hermes2.packet.json
```

Each export writes its trusted Worker public-key mapping beside the packet. Runtime/review never receive the approval private key.

To discard learning instead of exporting, use the separate rejection path:

```powershell
uv run python -m scripts.collective_learning --state-name hermes prepare-rejection
$disposition = Read-Host "Reviewed rejection disposition digest"
uv run python -m scripts.collective_learning --state-name hermes reject `
  --disposition-digest $disposition --rejected-by $operator `
  --reason "Assignment-specific guidance should not enter the shared role."
```

This signs `reject_and_refresh`, not export approval.

## 6. Evaluate and propose a Role Release

Prepare real baseline/candidate profile snapshots and an independently authored suite. Both profiles must have identical model/configuration/private state except for the exact delta covered by Hermes 2's approved packet:

```powershell
$hermesPython = Read-Host "Python executable with Hermes 0.19.0 installed"
uv run python -m scripts.evaluate_learning `
  --baseline-profile .local\evaluation\baseline `
  --candidate-profile .local\evaluation\candidate `
  --independent-suite .local\evaluation\holdout.json `
  --approved-packet .local\collective-learning\hermes2.packet.json `
  --worker-public-keys .local\collective-learning\hermes2.packet.worker-public-keys.json `
  --hermes-python $hermesPython --output .local\evaluation\result.json
```

Keep `AZURE_CONFIG_DIR` pointing to the existing operator cache when using an isolated profile; do not copy credentials. The runner makes actual model calls and reports independent cases separately from agent-proposed scenarios. Its scope is response-text behavior, not tool execution or native skill discovery.

Run central review against compatible packets:

```powershell
$nextRelease = Read-Host "Proposed newer semantic Role Release"
$reviewArgs = @(
  "--packet", ".local\collective-learning\hermes.packet.json",
  "--packet", ".local\collective-learning\hermes2.packet.json",
  "--worker-public-keys", ".local\collective-learning\hermes.packet.worker-public-keys.json",
  "--worker-public-keys", ".local\collective-learning\hermes2.packet.worker-public-keys.json",
  "--next-role-release", $nextRelease,
  "--decision-output", ".local\collective-learning\decision.json"
)
uv run python -m scripts.collective_review @reviewArgs
```

Inspect signatures, baseline/state bindings, retained evidence, conflicts, privacy results, and proposed `skills\role\<name>\SKILL.md` changes. After review, add `--create-pr` to create a draft Promotion PR.

The semantic review gates cover privacy, role alignment, learning evidence, and skill quality; triage supplies labels/context. Review workflow status with `gh aw status`. Automated reviewers cannot merge, mark ready, or refresh Workers.

After human review and merge, follow **Scheduling and release changes** in [DEPLOYMENT.md](DEPLOYMENT.md). In fresh sessions, verify shared promoted skills, distinct preserved private memories/playbooks, and retired previous-release candidates.

## 7. Scheduled work

Use Hermes 2 in an existing personal chat:

```text
Every 3 minutes, reply in this conversation with exactly "Scheduled hello from Hermes 2". Name the task scheduled-hello-demo.
```

Then:

```text
List my scheduled tasks.
Pause scheduled-hello-demo.
Resume scheduled-hello-demo.
Run scheduled-hello-demo now.
Remove scheduled-hello-demo.
```

Expect proactive output in the originating conversation and durable execution/delivery receipts. Only scheduling metadata enters Service Bus; the prompt stays on the Data Disk. The gateway remains awake while runtime compute may suspend. Remove the demonstration task afterward.

Automated deployed check:

```powershell
uv run python -m scripts.user_schedule_smoke `
  --state-name hermes2 --due-seconds 180 --timeout 1200
```

## 8. Documents and Agent User collaboration

```powershell
uv run python -m scripts.document_smoke --state-name hermes2 --timeout 1200
uv run python -m scripts.m365_actions_smoke `
  --state-name hermes2 --recipient user@contoso.com
```

Word smoke creates a real document, reads a marker, adds a comment/reply, and validates its URL. Remove its artifact afterward. Add `--execute` to the reviewed M365 actions command to send/read back an actual Agent User Teams message.

In a personal chat, attach a small DOCX containing recognizable text and a comment:

```text
Summarize this document and list its comments. Do not store its contents in memory or skills.
```

Expect real body/comment content. Requesting `/learn` from the attachment must block attachment-derived learning. An unsupported attachment must fail explicitly; limits are in [SPEC.md](SPEC.md).

On a file shared with the Agent User:

```text
Change only the stated handoff deadline to Friday 15:00 Europe/Prague.
Preserve the same document and comments; verify the edit before publishing.
```

Expect validated, ETag-protected same-item publication. If locked, choose **Keep trying original** or **Send shared copy now**. The copy must be explicitly accessible to the invoking user; it has independent sharing/history. A Word-comment request receives its result or exact not-applied proposal in that comment thread.

For Excel, request a specific shared-workbook range write/read-back. For notification handling, send an email or mention the Agent User in an accessible Word/Excel/PowerPoint comment; expect the originating workload response, not a new arbitrary Teams message.

## 9. Cards and generated apps

Personal-chat card prompt:

```text
Show a confirmation card asking whether I approve the delivery review agenda. Do not approve it yourself.
```

Expect a native governed card, a visible choice, and one Hermes continuation. Repeated action use must not repeat the continuation.

Generated-app prompt:

```text
Build a small delivery dashboard using only synthetic milestones. Test it and publish it for me. Show the app link and lifecycle controls.
```

Verify:

1. Anonymous access is rejected; the intended Entra participant can open the native URL.
2. Idle compute resumes through that URL.
3. The card offers **Open site**, **Keep 1h/6h/24h/72h**, and **Delete now**.
4. `What sites do I have running?` lists only the requesting user's apps.
5. An update keeps the logical app ID; a failed update leaves the previous app working.
6. Delete the app and request fresh inventory to confirm removal.

Apps run in separate child Sandboxes, not the Hermes process. Lifecycle actions use native APIs without a model turn or queue job. Treat the card as a snapshot; request inventory again after an action.
