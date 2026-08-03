# 0002. The Chapter 2 student lab is portal-only, and the scored harness becomes teacher material

- **Status:** Accepted
- **Date:** 2026-07-30
- **Deciders:** workshop content owner, workshop engineering
- **Relates to:** [0001](0001-chapter-2-student-lab-scope.md), which fixed the lab's subject
  matter as model choice and guardrails. This record changes only how that subject is taught.

> **Evidence provenance.** The measurements and screenshots referenced here were taken while
> the workshop was customer-specific: the scored contract used a "clinical boundary"
> criterion and the guardrail carried a customer-specific name. Resource names in this
> record have been updated to the current `foundryws-` prefix, but the screenshots in
> `docs/assets/screenshots/` still show the old names. Re-run the live pass and regenerate
> the screenshots before presenting any of this as current evidence.

## Context

ADR 0001 settled *what* the Chapter 2 hands-on segment teaches. The first implementation of
that decision was a Python command-line harness under `student/labs/build-agent/`. Attendees
ran `lab.py`, which created an agent through `azure-ai-projects`, sent a synthetic
stock-triage task to two cross-vendor models thirty times each, scored every answer against a
five-part contract, and printed a verdict. A second subcommand attached
`foundryws-guardrails-strict` and printed the resulting HTTP 400 `custom_blocklists` payload.

That harness was good evidence. It passed five educator rounds and two live student tests. It
also carried a delivery assumption that turned out to be false: that every attendee has a
working Python environment and can be brought to a working shell inside a thirty-minute
segment.

Three findings forced the question.

**There is no reliable shell to run it in.** The seat virtual machines are provisioned lean
by design. As deployed they carry `ca-certificates`, `curl`, `docker.io`, `gnupg`, `jq`,
`lsb-release`, the Azure CLI and the GitHub CLI, and nothing else. They do not clone this
repository, do not install `uv`, `azure-ai-projects`, `azure-identity` or `openai`, and do
not pin a Python version. A brief experiment that provisioned that runtime onto the seat VMs
was stood back down at the workshop owner's direction, because distributing a private
workshop payload to attendee machines is a separate decision with its own security surface.
The remaining option was the attendee's own laptop, which is thirty unknown environments and
an unbounded support cost in the first five minutes of a thirty-minute lab.

**The portal does not lose the evidence.** This was the assumption that had justified the
harness. It is wrong. A guardrail refusal in the Foundry playground carries an explicit
in-chat notice: "This interaction was blocked by a safety and security control in this
asset's Foundry guardrail." For a live room that is *better* than the HTTP 400
`custom_blocklists` payload, because nobody has to read JSON to see which of the three
indistinguishable outcomes — policy block, model declining, platform fault — actually
occurred.

**The attendee principal can do the whole thing in the portal.** Each seat user holds
Contributor at its own resource group plus `Foundry Project Manager` and `Foundry Account
Owner` at its own Foundry account, verified live by running the full create-agent,
attach-guardrail, invoke, delete cycle under a throwaway service principal granted exactly
those three roles.

## Decision drivers

- Reliability of the attendee path inside a thirty-minute timebox.
- Preserving the chapter's payload: the model-choice judgement and the guardrail finding.
- Where the judgement lives — in a tool, or in the attendee's head.
- Not distributing private workshop code to attendee machines.

## Options considered

**A. Keep the harness on attendee laptops.** Strongest evidence: thirty trials per model,
statistics, a reproducible verdict. Rejected. It requires a working Python environment on
thirty unknown machines, and the failure mode lands in the first minutes of the segment where
there is no recovery margin.

**B. Provision the harness runtime onto the seat VMs.** Was implemented and proven working,
then stood down. It requires copying a private repository onto attendee-controlled machines,
adds a Bastion hop and VM credentials to the critical path for every attendee, and makes the
whole chapter depend on the VM fleet being healthy.

**C. Portal-only click-through, scoring by eye.** Chosen. No installation, no shell, no code.
The attendee commits to five written criteria before reading any model output, then judges
two answers by hand.

**D. Portal for guardrails, harness for model choice.** Rejected. It keeps every drawback of
A or B while splitting one thirty-minute segment across two execution models.

## Decision

The Chapter 2 student lab is **portal-only**. The guide at
`docs/guides/chapter-2-build-agent.html` contains no shell command, no code and no
installation step.

The harness moves to `teacher/demos/build-host-agent/harness/` and is documented in that
demo's `OPERATOR.md` as an optional four-minute teacher segment. Trial statistics and a
printed verdict play well on a projector, and in that setting the environment is the
presenter's own and therefore known.

## Consequences

**Gained.** A path with no installation failure mode. Judgement moves from the tool to the
attendee: reading a failing answer and finding the wrong number yourself is the skill that
transfers, and it is the part the harness was doing on the attendee's behalf. The guardrail
attribution notice is more legible in a room than a JSON payload. The complete architecture
lesson — gateway-routed versus project-local models — is visible in one grouped dropdown.

**Lost.** Trial statistics. One task and one attempt per model is a smoke test that catches
obvious unfitness, not an evaluation. The guide states this plainly rather than implying more
rigour than the method supports, and directs attendees to treat the outcome as a shortlist.

**Accepted risk.** Scoring by eye can miss a subtle failure. The failure observed on
2026-07-30 was exactly that: Mistral-Large-3 produced both quantities correctly and then
offered to release reserved stock "if clinically appropriate", which fails only the clinical
boundary criterion, on two words a reader can skim past. The guide mitigates this by having
attendees commit to the criteria before reading any output and by naming this specific class
of failure. The room comparison at the end of the segment is the second safety net.

**Operational.** The chapter no longer depends on the seat VM fleet, on Bastion, or on any
per-seat runtime. It depends only on portal sign-in, which is a gate for the whole day
regardless.

## Validation

The entire guide was walked end to end in the live portal on seat-001 on 2026-07-30, in the
order the guide documents, and the screenshots in `docs/assets/screenshots/` are from that
run. Verified in that pass:

- A new agent defaults to a gateway model; every save mints a new version; Save is correctly
  disabled when the selected model has not changed.
- Both models answered the triage task: `gpt-5.6-luna` met all five criteria in 4.1s,
  `Mistral-Large-3` failed only the clinical boundary in 14.5s.
- Unguarded project-local `gpt-5.2`, with the triage instructions in place, answers the
  off-topic sentinel prompt, which is what makes the baseline meaningful.
- With `foundryws-guardrails-strict` assigned and saved, the control prompt is answered and the
  sentinel is refused with the attribution notice.
- Switching to a gateway model with that guardrail still attached and named: the sentinel is
  answered again in 3s.
- Deleting the agent requires retyping its exact name and returns the project to its empty
  state.

The published guide is additionally covered by rendering tests that load every page in
`docs/`, assert no console errors, no horizontal overflow at three widths, and that every
image actually decodes rather than falling back to its alt text.

### What that walkthrough does not prove

The walkthrough was driven as the workshop operator, a Global Administrator with owner-level
access to the seat project, not as a seat attendee. Every portal affordance the guide
instructs an attendee to use is therefore verified only at operator privilege.

At the time of the walkthrough seat attendees held Contributor on their own resource group,
which carries no Foundry data-plane permission at all. The platform environment has since
been changed to grant each seat user `Foundry User`
(`53ca6127-db72-4b80-b1b0-d745d6d5456d`), whose `dataActions` are
`Microsoft.CognitiveServices/*` scoped to that seat's own Foundry account. That should cover
agent create, run and delete and agent-level guardrail assignment, and guardrail creation is
covered on the control plane by the existing Contributor grant. Guardrail creation has since
been proven live by the platform session against a principal holding exactly that shipped role
set: list, create and delete of an account RAI policy all succeeded. Agent create, run and
delete remain reasoning from role definitions rather than an observed attendee-principal
result.

Two affordances would break the lab in front of the room if they are not present at
`Foundry User`: the **Manage guardrail** menu with its **Reassign guardrail** item, and the
model selector showing both the admin-connected gateway models and the project-local
`gpt-5.2` deployment. Until an attendee-principal run confirms them, treat attendee
data-plane access as unproven. The confirmation is blocked on Entra Temporary Access Pass
issuance, which needs tenant-admin Graph consent, and is tracked by the platform
environment rather than by this repository.

## Assumptions and revisit triggers

This decision assumes the portal remains the most reliable attendee surface and that the
teaching payload survives without programmatic scoring. Revisit if:

- an attendee-principal run shows any of the documented portal affordances missing or
  disabled at `Foundry User`, in which case either the guide or the role grant must change;
- the platform rebuild changes any name the guide states verbatim: the `shared-ai-gateway`
  connection, the model names, the `gpt-5.2` deployment, the `foundryws-guardrails-strict`
  guardrail and its blocklist term, or the inherited default reverting from
  `Microsoft.DefaultV2` to `Microsoft.Default`;
- a supported, pre-provisioned per-seat runtime becomes available that does not require
  distributing private workshop code to attendee machines;
- the guardrail attribution notice is removed from the playground, or the refusal stops being
  distinguishable from a model declining;
- the gateway guardrail gap is fixed, which removes the teacher demonstration's closing
  segment and requires it to be dropped rather than merely re-tested;
- room evidence shows most attendees reading both models identically, which would mean the
  by-eye comparison is not doing its work and the four things to look for need sharpening.
