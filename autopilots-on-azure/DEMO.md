# Hermes demonstration

Use existing `hermes` and `hermes2` Workers after [deployment validation](DEPLOYMENT.md). They share a role but not identities or Worker homes. Run personal-memory examples in **1:1 chats**, not shared groups. Use harmless synthetic facts throughout.

Commands run from `autopilots-on-azure`. [SPEC.md](SPEC.md) defines boundaries and current limitations.

## 1. Inference and tools

```powershell
uv run python -m scripts.demo_ops status --state-name hermes2
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
Remember my harmless marker LOTUS-81 and my preference for delivery-risk summaries with an owner and due date. This is personal memory, not a reusable skill.
```

Then send `/new`, followed by:

```text
Without retrieval tools or file reads, what is my marker and summary preference?
```

Expect fresh-session memory injection. `/new` changes the transcript, not durable memory.

Create a richer private procedure with Hermes' native `/learn`, which turns a request into a skill through `skill_manage`:

```text
/learn Private Playbook cedar-delivery in the private category. Project Cedar's harmless marker is CEDAR-42. Its weekly draft is due Thursday at 15:00 Europe/Prague. Verify owners and deadlines. Keep all Cedar-specific details private.
```

After `/new`:

```text
/cedar-delivery What is the marker and weekly draft deadline?
```

Expect progressive skill loading rather than mandatory injection into every prompt.

| Inspect in the Worker home | Meaning |
| --- | --- |
| `memories\USER.md`, `memories\MEMORY.md` | Personal Memory |
| `skills\private\cedar-delivery\SKILL.md` | Private Playbook |
| `state.db` and session data | Work History |
| `skills\role\` | Inherited Role Skills |
| `skills\candidates\` | Local reusable additions |
| Native skill ledger | Every skill change, with before/after content |
| `learning\receipts\` | The owner's decisions on learned changes |

## 3. Local reusable learning

On Hermes 2:

```text
/learn Candidate Improvement dependency-handoff-contract in the candidates category. A dependency handoff must record provider, receiver, deliverable, acceptance criteria, and needed-by date. Keep it general and use one SKILL.md only.
```

After `/new`:

```text
/dependency-handoff-contract What must every dependency handoff record?
```

Expect a native skill write that the ledger records; the change is local to Hermes 2. As the Worker owner, send `/learning` in your personal chat. The digest card shows the diff with **Undo** and, for this Candidate Improvement, **Share**, **Keep** or **Discard**. Choose **Share** to make the current content eligible for the next Learning Packet. With `HERMES_SKILL_WRITE_APPROVAL` enabled, the write first appears under staged writes with **Approve**/**Reject**.

For multi-Worker review, independently teach Hermes a related generalized correction, such as requiring explicit confirmation of a handoff date and its timezone. Do not copy private Cedar details or manufacture agreement between Workers.

Background review, Curator and Dreaming write through the same native tools, so their changes appear in the same digest. A governed change without a complete ledger trace, such as a direct file edit, is shown but stays out of packets.

## 4. Dreaming

Create an ordinary observation:

```text
Analyze this pattern without changing memory or skills: three handoffs accepted dates copied from notes; one lacked a timezone, another an accountable confirmer, and the third a traceable source. What should future handoffs verify?
```

Then run an ad-hoc queue-backed Dream without changing production cron:

```powershell
uv run python -m scripts.servicebus_dream_smoke `
  --state-name hermes2 --timeout 1800
```

Expect a private memory/playbook change, a governed create/patch recorded in the native ledger, or no change when already represented; the owner receives the learning digest afterwards. Dreaming is not required to invent a skill. The smoke uses the Sandbox management plane to enqueue a distinct occurrence on the existing scheduling queue; it does not change production cron or prepare a Learning Packet.

## 5. Prepare, review, approve, export

Prepare both Workers independently:

```powershell
uv run python -m scripts.collective_learning --state-name hermes prepare
uv run python -m scripts.collective_learning --state-name hermes2 prepare
```

The CLI uses the signed-in Azure CLI user, not the runtime API key. Run `scripts.setup_identity` and redeploy first to configure the dedicated Entra operator API. Only each Worker's configured `worker_owner_id` may inspect, approve, export or reject packets. If a scope-specific login is needed, follow the CLI's `az login --use-device-code --tenant ... --scope ...` command. The gateway validates signature, tenant, audience, delegated scope, Azure CLI client and owner object ID; `approvedBy`/`rejectedBy` come from that principal, never an alias.

Inspecting before preparation returns an explicit runtime `400` with “Prepare the Learning Packet before approval,” not an empty packet. Runtime HTTP errors retain their source/status; unavailable runtime RPCs return `502` and timeouts `504`. These are not successful approvals or exports.

Review the returned improvements and exclusions. Packet 3.0 contains only skills whose current content the owner shared, each with its native ledger chain; `excluded` names every other governed change and why it stays local. It never contains Personal Memory, Private Playbooks, documents, or raw Work History.

Approve each exact returned digest:

```powershell
$hermesDigest = Read-Host "Reviewed Hermes packet digest"
$hermes2Digest = Read-Host "Reviewed Hermes 2 packet digest"
uv run python -m scripts.collective_learning --state-name hermes approve `
  --packet-digest $hermesDigest
uv run python -m scripts.collective_learning --state-name hermes2 approve `
  --packet-digest $hermes2Digest

New-Item -ItemType Directory -Force .local\collective-learning | Out-Null
uv run python -m scripts.collective_learning --state-name hermes export `
  --output .local\collective-learning\hermes.packet.json
uv run python -m scripts.collective_learning --state-name hermes2 export `
  --output .local\collective-learning\hermes2.packet.json
```

Each export writes its trusted Worker public-key mapping beside the packet. Runtime/review never receive the approval private key. The gateway signs only the exact pending digest; later governed-state changes invalidate export. Teams approval of a staged skill write remains a separate owner action, not Learning Packet approval.

To discard learning instead of exporting, use the separate rejection path:

```powershell
uv run python -m scripts.collective_learning --state-name hermes prepare-rejection
$disposition = Read-Host "Reviewed rejection disposition digest"
uv run python -m scripts.collective_learning --state-name hermes reject `
  --disposition-digest $disposition `
  --reason "Assignment-specific guidance should not enter the shared role."
```

This signs `reject_and_refresh`, not export approval.

## 6. Evaluate and propose a Role Release

Prepare real baseline/candidate profile snapshots and an independently authored suite. Both profiles must have identical model/configuration/private state except for the exact delta covered by Hermes 2's approved packet:

```powershell
$hermesPython = Read-Host "Python executable with Hermes installed"
uv run python -m scripts.evaluate_learning `
  --baseline-profile .local\evaluation\baseline `
  --candidate-profile .local\evaluation\candidate `
  --independent-suite .local\evaluation\holdout.json `
  --approved-packet .local\collective-learning\hermes2.packet.json `
  --worker-public-keys .local\collective-learning\hermes2.packet.worker-public-keys.json `
  --hermes-python $hermesPython --output .local\evaluation\result.json
```

Keep `AZURE_CONFIG_DIR` pointing to the existing operator cache when using an isolated profile; do not copy credentials. The runner makes actual model calls on the independent suite. Its scope is response-text behavior with skill content in the prompt, not full native skill/tool discovery. Use the same pinned Hermes version for both profiles; the deployed dependency base provides Hermes 0.22.6 through `/app/.venv/bin/python`. Do not infer approved-feed package availability from the upstream image version.

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

Expect proactive output in the originating conversation and durable execution/delivery receipts. Only scheduling metadata enters Service Bus; the prompt stays in the Worker home. A native receiver owns waiting, so the gateway, runtime, and both MCP services can all suspend to Memory. Delivery can lag the due time because managed source polling is not instantaneous. Remove the demonstration task afterward.

Automated deployed check:

```powershell
uv run python -m scripts.user_schedule_smoke `
  --state-name hermes2 --due-seconds 180 --timeout 1200
```

## 8. Documents and Agent User collaboration

Validate through the real Agent User in Teams; there is no operator model endpoint. In a personal chat, ask Hermes to create a private Word document with a unique harmless marker, add a comment and reply, and return its URL. Then request a separate read-only read-back of the body, comment, and reply. Inspect and remove the test document through its Microsoft 365 storage surface afterward.

To validate proactive Teams delivery, use a personal chat and explicitly identify the intended recipient:

```text
Use workiq-teams to create or reuse a one-to-one chat with <recipient UPN>,
then send this exact harmless test message: "Autopilot validation <unique marker>".
Return the chat and message identifiers only after both operations succeed.
```

Confirm the message in the intended chat. This sends a real Teams message.

To validate attachment handling, attach a small DOCX containing recognizable text and a comment:

```text
Summarize this document and list its comments. Do not store its contents in memory or skills.
```

Expect real body/comment content. Requesting `/learn` from the attachment must block attachment-derived learning. An unsupported attachment must fail explicitly; limits are in [SPEC.md](SPEC.md).

On a file shared with the Agent User:

```text
Change only the stated handoff deadline to Friday 15:00 Europe/Prague.
Preserve the same document and comments; verify the edit before publishing.
```

Expect validated, ETag-protected same-item publication. If another user saves the document first, nothing is overwritten and the edit is reapplied to the latest version. If locked, choose **Keep trying original** or **Send shared copy now**. The copy must be explicitly accessible to the invoking user; it has independent sharing/history. A Word-comment request receives its result or exact not-applied proposal in that comment thread.

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
