# Goal Card: Fresh Worker bootstrap

## OBJECTIVE

Card: G08 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Prove repeatable fresh Worker provisioning using blueprint-first,
endpoint-later setup on ACA Sandboxes, without hidden existing tenant state,
stored client secrets or endpoint replacement.

## OUTPUT

Shared artifacts plus a proposed primary no-argument orchestration sequence,
permission/prerequisite checklist and sanitized provisioning/read-back receipt.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Dependency graph and effective CLI options verified: blueprint without endpoint, MI federation, Agent Identity/User, deployment, endpoint registration, permissions | Inspect installed CLI help/dry-run and setup scripts; record roles and owner-required consent steps | Correct order; missing CLI/role prerequisite is BLOCKED | C01-C04 |
| C02 | Two approved fresh Workers provision through the same scripted sequence with no reused blueprint/User/disk/keys and no secret workaround | Real state read-back of distinct identities, groups, profile disks and endpoints; record exact commands and IDs privately | Repair isolated orchestration; do not alter first/current Worker to rescue second | C02-C04 |
| C03 | Each Worker handles one Teams event, mail/Word notification fixture and authorized MCP access; replaying orchestration does not duplicate identities/endpoints | Verify channel receipts and object counts before/after rerun; roles/permissions actually match plan | Fix idempotent lookup/read-back; unknown create outcome blocks retry | C03-C04; C02 if provisioning changes |
| C04 | Demonstration needs no undocumented manual portal repair; unavoidable admin/auth interactions explicit; cleanup complete within approved scope | Executor reconciles receipts and final resource inventory; compare old circular dependency against new sequence | Document precise blocker; no fake no-argument success path | C04 and affected checks |

## QUALITY

CLI defaults may still create secrets; inspect actual behavior (C01-C02).
An endpoint update is not a fresh registration. A successful ARM apply alone
does not prove usable Agent User messaging (C03).

## CONTEXT

`DEPLOYMENT.md`; `scripts\setup_agent365.py`, `setup_identity.py`,
`provision_agent365_instance.py`, `sandbox_services.py`; shared A365 setup
reference. Existing Worker configuration is evidence, not reusable state.

## CONSTRAINTS

Shared contract applies. Bind disposable identity prefixes and cleanup before
creating anything. Consent/registration needs owner authority. Keep
device-code login interaction explicit; do not change current endpoints.

## STAGES

1. Verify prerequisites and dependency order without mutations.
2. Provision and exercise two independent disposable Workers.
3. Reconcile idempotency/cleanup and deliver the proposed bootstrap.

## STOP-CAPS

Pilot cap: 3 cycles, 180 minutes, two Worker provisions, two orchestration
rechecks and 24 channel/tool test operations. Stop after 3 cycles without closing
a provisioning/behavior gap. DONE requires C01-C04 PASS; missing Frontier access,
consent or authority BLOCKED, limits CAPPED, user stop CANCELLED.
