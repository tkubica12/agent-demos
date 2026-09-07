# ADR 0016: Managed Office tools with reviewed local editing

## Decision

Use Work IQ for supported Microsoft 365 operations; use Agent User Graph plus reviewed local tooling for specific editing gaps.

| Layer | Owns |
| --- | --- |
| Work IQ | Discovery, creation, semantic reads, comments/replies, sharing, and workload messaging. |
| Graph under Agent User | Download, same-item ETag publication, permissions, attribution, version history, and Excel range writes. |
| Image-pinned MiniMax Office tools | Local package inspection/editing, Microsoft Open XML validation, and rendering where needed. Preserve MIT notices. |
| Loopback collaboration MCP | Fixed wrappers and private operation lifecycle, not Office-format mutation algorithms. |

Download privately, inspect, apply the smallest requested change, verify its semantics, validate, and publish with the source ETag. Target Microsoft 365 Open XML. Record existing validation defects as a private baseline; newly introduced defects block upload. Never repair unrelated source defects silently.

Prefer same-item publication to preserve sharing, comments, and version history. Apply body changes before comments/mentions. Guarded text-node edits can rebase on a new ETag; opaque transformations fail closed.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Work IQ alone | Does not cover arbitrary existing Word body edits. |
| Custom Office-format engine in the gateway | Duplicates maintained tooling and couples document behavior to transport. |
| Always return a new file | Breaks same-document collaboration and inherited sharing/history. |
| SharePoint checkout to bypass a lock | Blocks coauthors rather than cooperating with them. |

Dependencies are baked into the runtime image, not installed during a turn. Document bytes and retained edits stay in private Worker storage, outside memory/skill exports.

For persistent locks, use [ADR 0019](0019-durable-document-publish-and-interactive-choice.md). Reconsider local mutation when Work IQ exposes a supported coauthoring-compatible body/range-edit API.

## References

- [Work IQ Word](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-word-work-iq)
- [Graph same-item upload](https://learn.microsoft.com/en-us/graph/api/driveitem-put-content)
- [Graph Excel API](https://learn.microsoft.com/en-us/graph/api/resources/excel)
