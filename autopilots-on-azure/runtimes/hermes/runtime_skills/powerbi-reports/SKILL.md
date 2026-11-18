---
name: powerbi-reports
description: Answer questions about Power BI reports and semantic models read-only through the Fabric IQ MCP server under the Worker's own Agent User identity.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    category: analytics
    tags: [Power BI, Fabric IQ, DAX, reports, read-only]
---

# Power BI reports (read-only)

Use this skill when a user asks which Power BI reports you can see, what a report contains, or for a figure from a Power BI report or semantic model.

The `powerbi` MCP server is Microsoft Fabric IQ. It runs as your own Agent User, so you see exactly the reports and rows you were granted. Use `tools/list` for the live tools; usually `DiscoverArtifacts`, `ResolveFabricItem`, `GetReportMetadata`, `GetSemanticModelSchema`, `ValueSearch`, and `ExecuteQuery`.

## Boundaries

- If no `powerbi` tools are available, say Power BI access is not configured for you.
- Power BI is read-only for you: never create, change, refresh, share, or export anything, and never offer to.
- Answer only from tool results in this turn, never from memory. Do not save report data or query results to memory or skills.
- On a permission or not-found error, say you do not have access to that item. Do not look for another route.

## Steps

1. Listing reports: call `DiscoverArtifacts` with the user's search text (for example `MVNO`) and list each report by exact name and workspace, with the count.
2. One report: find it (`DiscoverArtifacts`, or `ResolveFabricItem` for a URL), call `GetReportMetadata` and note the fields and report, page, and visual filters, then `GetSemanticModelSchema` to pick the exact column the report uses. Use `ValueSearch` for the stored spelling of a value.
3. Run the smallest DAX query that answers the question with `ExecuteQuery`, applying the report's filters, for example `EVALUATE ROW("Channels", COUNTROWS(VALUES('Sales'[Sales channel])))`. If you cannot reproduce a filter, say so.
4. Answer with the result, report name, column, and filters used.

## Data minimisation

Return only what was asked. When the user says not to include amounts or counts, give no monetary values, revenue, or sales counts. Never paste raw fact rows unless asked for a few examples.
