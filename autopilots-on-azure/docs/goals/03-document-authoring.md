# Goal Card: Reliable document authoring

## OBJECTIVE

Card: G03 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Determine whether supported server-side operations can replace local DOCX
download/edit/reupload while retaining same-document collaboration. A negative
answer is valid when supported by inspected contracts and actual tool discovery.
Do not redesign around a new file or a permanently open Office client.

## OUTPUT

Shared artifacts plus a capability matrix and repeatable, approved disposable
document test procedure. Report Word separately from any Excel capability.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Inventory covers Word workload MCP, unified Work IQ schema/path discovery, supported Graph operations and documented host-only APIs | Executor records actual schemas/catalog and supporting passages; identify body/range edit versus metadata/create/comment | Resolve a missing schema; catalog access denial is BLOCKED, not absence | C01-C04 |
| C02 | A supported candidate exists, or documented limitations plus live catalog discovery show no eligible body-edit candidate in the bounded inventory | Compare C01 operations with unattended same-item authoring; record branch evidence | Investigate one contract ambiguity; missing decisive evidence blocks | C02-C04 |
| C03 | If eligible, complete both arms of 10 frozen concurrent-edit/lock/stale-version/timeout/duplicate cases. ADOPT requires zero lost edits, preserved item/sharing/comments/history, and no whole-file reupload/copy for successful body edits. Otherwise evidence supports REJECT; C02-ineligible branch records no mutations | Approved coauthor/test runner; inspect actual content and permission/version/comment read-backs, including failed outcomes | Repair isolated wrapper; reject invariant failure; never force unlock or check out | C03-C04 |
| C04 | Decision retains ADR 0019 unless candidate adoption gates pass; eligible comparison reports operation counts, cleanup and 10% latency guardrail outcome | Reconcile saved result and all branch evidence; failed completed adoption comparison means REJECT, not missing evidence | Correct claim; unresolved performance/correctness is BLOCKED/CAPPED | C04 and changed checks |

## QUALITY

The ten cases and expected artifacts must be frozen before execution. Include
plain text, table and formatting-preserving edits. Office.js coauthoring in a
host is not an autonomous cloud Word API (C01-C02). A read-back is authoritative,
not the agent's "updated" message (C03).

## CONTEXT

ADR 0016/0019; `autopilots_identity\document_operations.py`,
`collaboration_mcp.py`; shared Word and Work IQ references. G02 must establish
identity eligibility before candidate Work IQ mutations.

## CONSTRAINTS

Shared contract applies. Only named disposable files and approved coauthors.
No current documents, resharing to new recipients, lock bypass or body content
in telemetry. Unknown mutation outcome requires read-back before retry.

## STAGES

1. Establish eligible APIs and freeze collaboration cases.
2. Test real edits or substantiate bounded rejection.
3. Compare, clean up and deliver the decision.

## STOP-CAPS

Pilot cap: 3 cycles, 180 minutes, 120 service calls including at most 40 document
mutations. Stop after 3 cycles without a resolved gap. DONE requires C01-C04
PASS on either fully evidenced branch; missing catalog/coauthor access BLOCKED,
limits CAPPED, user stop CANCELLED. Preserve partial evidence.
