# ADR 0016: Office document collaboration through managed tools, a thin Graph bridge, and permissive skills

- Status: Accepted
- Date: 2026-07-25

## Context

The Worker must read, create, review, modify, and return Microsoft 365 documents while actions remain attributable to its Agent User. Required behaviors include same-item Word updates with version history, Word comments and replies, Excel collaboration, PowerPoint handling where practical, PDF processing, proactive Teams file delivery, and deletion of temporary local copies.

No single Microsoft-managed tool currently covers this contract:

- Work IQ Word creates new documents, reads text and comments, creates comments, and replies to comments, but does not edit an existing document body or create tracked changes.
- Work IQ Excel in this tenant creates and reads workbooks and handles comments, but does not expose general range writes.
- Microsoft Graph provides Agent User-attributed file download, upload, version history, directory profiles, and a mature Excel workbook API, but no Word or PowerPoint body object model.
- Microsoft 365 Copilot Cowork and Scout have closed document capabilities that are not callable from a custom Agent 365 runtime.

Hermes 0.18 installed from PyPI contains no bundled DOCX, XLSX, PPTX, or PDF skills. Nous Research's source repository includes document skills adapted from Anthropic, but each document skill retains Anthropic's proprietary `LICENSE.txt`, which prohibits copying, retaining, deriving, and distributing the material outside Anthropic services. The repository's root MIT license does not override those file-local terms. OpenClaw does not bundle those skills either.

The implementation therefore needs a legal, maintainable local document capability without reimplementing Office formats inside the identity bridge.

## Options considered

### Microsoft-managed Work IQ only

Use Word, Excel, OneDrive, SharePoint, Teams, Mail, Calendar, and M365 Copilot MCP servers without local editing.

Rejected because existing Word body edits, tracked changes, arbitrary Excel range writes, and PowerPoint body edits are missing.

### Anthropic-derived Hermes document skills

Copy or install the DOCX, XLSX, PPTX, and PDF skills found in the Hermes source repository.

Rejected because their per-skill proprietary license has no internal, demo, or noncommercial exception. Running an official Nous image is lower practical distribution risk than copying the files into this repository or image, but it does not provide a clear license to retain and use the skills outside Anthropic services.

### OpenClaw or Microsoft Scout document capability

Reuse the document implementation used by OpenClaw or Scout.

Rejected because OpenClaw has no bundled DOCX, XLSX, or PPTX editor; it provides read-only PDF extraction and optional external skills. Scout is closed and exposes no reusable document API or public skill implementation.

### Custom Python Office manipulation MCP

Keep package-level DOCX/PPTX XML mutation and extraction directly in `collaboration_mcp.py`, using Python libraries where useful.

Rejected as the primary architecture because it mixes identity, transport, document semantics, and validation in one custom service. Python libraries are useful for constrained tasks, but python-docx and python-pptx do not provide complete tracked-change or format-fidelity behavior, and openpyxl is inferior to Graph workbook APIs for live collaboration.

### Original Microsoft Open XML helper and skill

Maintain our own .NET CLI using Microsoft's MIT Open XML SDK.

Viable and license-clean, but superseded for Word by the more complete `MiniMax-AI/skills` implementation. A small local adapter may remain only where the selected upstream CLI lacks a fixed noninteractive operation.

### OneNote pages through Microsoft Graph

Use OneNote as a collaborative document-like surface. Microsoft Graph exposes stable page creation and granular HTML element updates through `POST /onenote/pages` and `PATCH /onenote/pages/{id}/content`, including append, insert, and replace operations against current element IDs. This is materially better than replacing a complete DOCX package and could support practical human and Agent User contributions with lower collision risk.

Deferred rather than rejected. The OneNote API requires delegated permissions, which an Agent User may be able to satisfy, but this needs live validation for identity attribution, concurrent human edits, Teams tabs, permissions, and element-ID refresh behavior. OneNote is also not the newest Microsoft collaboration surface. Before adding it, validate with target customers that OneNote is present in their actual workflows and that introducing another artifact type improves their experience.

Review this option when customer discovery demonstrates meaningful OneNote adoption. A focused feasibility spike should then prove Agent User `Notes.ReadWrite` access, granular updates, attribution, and concurrent client behavior before any architecture change.

### Microsoft Loop and Copilot Pages

Use Loop pages/components or Copilot Pages as the canonical living collaborative artifact. These provide the strongest current Microsoft user experience for real-time coauthoring, presence, shared cursors, portable components, and Teams integration. They are strategically more promising than Word for continuous human-and-agent collaboration because their Fluid-based data model applies granular shared-state operations rather than external binary replacement.

Deferred because no supported public server-side write contract currently lets this custom Agent 365 runtime participate in an existing Loop or Copilot Page Fluid session. Public Graph surfaces can discover, govern, and export underlying artifacts, but no documented API patches arbitrary page blocks as an attributed live coauthor. There is no current Work IQ Loop/Pages MCP server or skill in the tenant catalog.

Internal research found a Microsoft-internal pre-alpha Loop CLI that talks directly to the Loop Web Service and supports page create, read, append, and replace operations. It is explicitly intended for internal experiments and is not a supported customer or production dependency. Its Agent User, service-principal, attribution, public Graph, Work IQ, MCP, and roadmap contracts are undocumented, so it must not be adopted or reverse-engineered here.

Monitor Loop as the preferred future replacement for this offline DOCX publication path. Re-evaluate when Microsoft publishes a supported Graph, Work IQ, MCP, skill, or equivalent server-side API that:

1. creates and updates Loop or Copilot Page blocks without whole-artifact replacement;
2. supports Agent User or another governed autonomous identity;
3. preserves attribution, permissions, history, and concurrent human edits;
4. is supported for customer production use.

### MiniMax permissive document skills plus Microsoft services

Use the MIT-licensed MiniMax skills, pinned to reviewed commit `60aaae52bb2af8162732751a4332f62a5fef518b`:

- `minimax-docx`, which uses Microsoft Open XML SDK and provides create, edit, fill, template, diff, tracked-change helpers, and validation pipelines;
- `minimax-xlsx` only for offline transformations not better served by Graph workbook APIs;
- `pptx-generator` for PowerPoint creation and constrained local editing;
- `minimax-pdf` for PDF creation, form filling, and reformatting.

This option is legal to redistribute under MIT and reuses a maintained skill implementation rather than copying Anthropic materials or expanding custom format code.

## Decision

Use a layered Office collaboration architecture:

1. Prefer Microsoft-managed Work IQ tools for Word creation, semantic reads, comments, replies, file discovery, sharing, Mail, Teams, Calendar, and notifications.
2. Use Microsoft Graph under the Agent User identity for deterministic user resolution, private file download, same-item upload, ETag conflict protection, version history, `lastModifiedBy` attribution, local cleanup, and Excel workbook range operations.
3. Keep `m365-collaboration` as a thin loopback-only MCP boundary. It may expose fixed wrappers around reviewed document CLIs, but it must not contain Office-format mutation algorithms.
4. Pin the selected MiniMax skill source to commit `60aaae52bb2af8162732751a4332f62a5fef518b`, preserve its MIT license and notices, and review every future pin change.
5. Bake required dependencies into the immutable runtime image. Do not install packages dynamically during a user turn.
6. Execute the workflow as download to a private per-turn directory, inspect, apply the smallest requested edit, validate with MiniMax business rules and Microsoft Open XML SDK, optionally render for visual inspection, upload with the original ETag, and delete the local copy on success or failure. MiniMax's subset XSD is advisory because it can reject valid Microsoft 365 relationship patterns.
7. Prefer Graph workbook APIs over local XLSX round-trips for ordinary collaborative range operations.
8. Keep consequential writes explicit, workload-appropriate, and attributable. Retry transient SharePoint coauthoring locks and throttling (`423`, `429`, `503`) with bounded backoff; fail on ETag conflicts, persistent locks, validation failures, unsupported edits, or ambiguous target content rather than guessing.
9. Treat a document CLI's success status as insufficient. Fixed wrappers must independently verify the requested semantic change with Microsoft MarkItDown before upload.
10. Keep same-item collaboration as the default. A `423 Locked` response retains the already validated edit in Worker-private storage under an opaque operation ID for one bounded hour. The Worker reports progress and retries only the publish operation in short intervals for approximately two minutes.
11. If a stable Word text-node patch encounters a changed source ETag during retry, download the latest source, reapply the guarded edit intent, revalidate it, and publish with the new ETag. Generic local edits fail closed because their transformation intent cannot be safely reconstructed.
12. After a persistent lock, ask the user to choose between retrying the shared original later, receiving an edited copy now, or cancelling. Never create a fallback copy silently. An explicitly requested copy is written to the Agent User's `Hermes Results` folder and returned through Work IQ Teams; it has an independent sharing, comment, and version history.
13. Do not use SharePoint checkout to recover from coauthoring locks. Checkout intentionally prevents other people from editing. Resumable upload sessions improve transfer reliability but do not bypass an Office lock.
14. Apply body changes before adding comments or mentions so a subsequent comment-side ETag change cannot invalidate the body publish.
15. Bind each pending publish to a one-way hash of a stable private conversation scope. The runtime regenerates the scope on every turn, allowing `find_pending_office_publishes` to recover an internal operation ID after native transcript rotation without exposing the ID or persisting it in Hermes memory.
16. Validate Open XML packages against `FileFormatVersions.Microsoft365`. The SDK's parameterless validator defaults to Office 2007 and falsely rejects standard modern Word attributes such as `w14:paraId`.
17. For existing documents, record a private Open XML baseline immediately after download and compare the edited package against it. Unchanged source errors remain visible but do not block an unrelated edit; any newly introduced error remains a hard failure. Do not silently repair unrelated source defects.
18. A fallback copy in the Agent User's OneDrive must receive an explicit user-specific Graph permission before Teams delivery. Passing an existing `fileUrl` to Work IQ Teams attaches the URL but does not guarantee that the recipient can open it. `publish_pending_office_copy` therefore requires the recipient identifier, grants write access with `driveItem: invite`, verifies a permission response, and deletes the new copy if sharing fails.
19. Preserve sanitized Graph lock diagnostics (`status`, error code/message, retry hint, request ID) in structured locked outcomes. A long-lived `423` must not be described as ordinary coauthoring without the service error details.
20. Microsoft documents WOPI lock refresh as resetting an automatic 30-minute expiration timer. A WOPI client or Microsoft 365 service can refresh the timer, so a lock can outlive ten minutes even when the user has no desktop document open. Graph binary replacement can return `423 notAllowed` without the WOPI lock ID or holder. Report that limitation and the request ID; do not infer a named user.
21. Use ADR 0019's predefined Teams choice card and durable Service Bus operation when foreground retries cannot publish the original. Review both ADRs when Work IQ Word offers a supported coauthoring-compatible server-side body or range edit API.
22. Keep OneNote as a customer-validation option rather than adding it speculatively. Keep Loop/Copilot Pages as the preferred strategic watchlist item and review their public autonomous write surfaces alongside Work IQ Word.

## Dependency policy

The initial immutable runtime includes only dependencies required by enabled paths:

- .NET and `DocumentFormat.OpenXml` for MiniMax DOCX;
- Microsoft MarkItDown for bounded read normalization;
- LibreOffice and Poppler for rendering and visual verification where layout matters;
- Graph workbook APIs for ordinary Excel collaboration;
- PptxGenJS only when PowerPoint creation is enabled;
- permissively licensed PDF libraries only when PDF generation or form filling is enabled.

PyMuPDF is excluded because its AGPL/commercial license is incompatible with the desired dependency policy. Anthropic document skill files and derivatives are excluded.

## Consequences

- Microsoft 365 identity, access, attribution, versioning, and delivery stay separate from local document manipulation.
- Word behavior becomes broader and better validated without maintaining a bespoke Word engine.
- Locked same-item writes retain the collaboration-first UX while giving users an explicit immediate-copy escape hatch.
- Retained edits contain private document bytes and edit intent, remain only in the Worker-private collaboration workspace, are addressed by opaque IDs that are never shown to users, and expire after a bounded hour.
- Pending operations are isolated by a hashed conversation scope. This is continuity metadata, not document content, and survives native transcript rotation only in the private operation manifest.
- Existing documents can contain Word-tolerated legacy defects. Baseline-delta validation preserves those defects without blessing new ones or changing unrelated formatting.
- A fallback copy is not equivalent to collaboration on the original: it starts a separate version, comment, mention, and sharing history.
- The Sandbox image becomes larger because document rendering and validation require additional runtime dependencies.
- Preview Work IQ tool names and tenant catalog availability can change; live tool discovery and repeatable smokes remain required.
- MiniMax is not a Microsoft product. Its value is a clear MIT license and its use of Microsoft's official Open XML SDK; the source pin and dependency tree remain supply-chain review points.
- PowerPoint has no tracked-changes model, and PDF has no Office-style tracked changes.
- Notification ingress remains unchanged: Agent 365 email and Office comment events continue to use the existing `/api/messages` Activity Protocol endpoint.

## References

- [Work IQ tooling catalog](https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview)
- [Work IQ Word reference](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-word-work-iq)
- [Microsoft Graph Excel API](https://learn.microsoft.com/en-us/graph/api/resources/excel)
- [Microsoft Graph upload or replace drive item content](https://learn.microsoft.com/en-us/graph/api/driveitem-put-content)
- [Microsoft Graph resumable upload sessions](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession)
- [Microsoft Graph drive item checkout](https://learn.microsoft.com/en-us/graph/api/driveitem-checkout)
- [WOPI Lock](https://learn.microsoft.com/microsoft-365/cloud-storage-partner-program/rest/files/lock)
- [WOPI RefreshLock](https://learn.microsoft.com/microsoft-365/cloud-storage-partner-program/rest/files/refreshlock)
- [Microsoft Graph OneNote page updates](https://learn.microsoft.com/en-us/graph/onenote-update-page)
- [Loop, Copilot Pages, and Copilot Notebooks comparison](https://support.microsoft.com/en-us/Microsoft-365-Copilot/compare-microsoft-loop-copilot-pages-and-copilot-notebooks)
- [Loop and Copilot Pages SharePoint Embedded governance](https://learn.microsoft.com/en-us/microsoft-365/loop/spe-management)
- [Microsoft Open XML SDK](https://github.com/dotnet/Open-XML-SDK)
- [MiniMax skills](https://github.com/MiniMax-AI/skills)
- [MiniMax DOCX skill license](https://github.com/MiniMax-AI/skills/blob/main/skills/minimax-docx/LICENSE)
- [OpenClaw](https://github.com/openclaw/openclaw)
- [Anthropic skills licensing notice](https://github.com/anthropics/skills/blob/main/skills/docx/LICENSE.txt)
