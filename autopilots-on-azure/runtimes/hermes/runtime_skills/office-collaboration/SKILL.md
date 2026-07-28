---
name: office-collaboration
description: Collaborate on Microsoft 365 Word, Excel, PowerPoint, and PDF files through Agent User identity, local verified edits, and version-safe publishing.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    category: productivity
    tags: [Microsoft 365, Word, Excel, PowerPoint, PDF, collaboration]
---

# Microsoft 365 Office collaboration

Use this skill when a user asks to inspect, create, edit, review, or return a Microsoft 365 Office file.

For Word operations, use the fixed `m365-collaboration.minimax_docx_*` tools instead of loading the upstream `minimax-docx` skill text or running its shell commands. The reviewed MIT source and Microsoft Open XML implementation are pinned and baked into those wrappers; loading the full upstream skill wastes document-turn context.

## Identity and file lifecycle

1. Keep Work IQ Word for Word creation, semantic reads, comments, and replies.
   After creating a document, call `m365-collaboration.share_office_file_with_user` for every intended participant with the required role, verify the permission result, and only then deliver the URL through Teams.
2. Keep Graph workbook range tools for collaborative Excel reads and writes.
3. For local Word, PowerPoint, or PDF work, call `m365-collaboration.download_office_file` with the Microsoft 365 sharing URL.
   Pass the private `operationScope` supplied by the runtime instructions to every edit, retry, copy, cancel, and pending-operation lookup. Never reveal it or write it to memory.
4. Work only on the returned `localPath`. Never move the file outside the private collaboration workspace.
5. Inspect content with `m365-collaboration.read_local_office_file`, which uses Microsoft MarkItDown with bounded output.

6. Apply the smallest requested change. Do not redesign formatting or change unrelated content.
   Fixed MiniMax wrappers perform an independent MarkItDown before/after assertion. A CLI success message without the requested semantic result is a hard failure.
7. Validate DOCX with `m365-collaboration.minimax_docx_validate`, which combines MiniMax business rules with Microsoft 365-targeted Open XML SDK validation. Downloads record a private baseline: unchanged source errors are reported but allowed, while any error introduced by the edit blocks upload. The MiniMax subset XSD is intentionally not a hard gate because valid Microsoft 365 documents can use relationship patterns outside that subset. Validate XLSX or PPTX with `validate_local_office_file`.

8. For visual QA, render a temporary PDF with LibreOffice and inspect the pages before upload when layout matters:

   ```bash
   soffice --headless --convert-to pdf --outdir "<workdir>" "<localPath>"
   pdftoppm -jpeg -r 100 "<rendered.pdf>" "<workdir>/page"
   ```

9. Upload only through `m365-collaboration.upload_office_file` using the original sharing URL, local path, and returned `expectedETag`. An ETag conflict means another human changed the document; never overwrite it.
10. After success, validation failure, or a non-retryable upload failure, call `m365-collaboration.delete_local_office_file`. Do not delete a file retained by a structured `locked` result; the pending publish operation owns its bounded cleanup.
11. Report the returned `lastModifiedBy`, `eTag`, and `webUrl`. Use Work IQ Teams file tools when the user wants the result delivered in Teams.
12. For long Teams workflows, send concise milestone updates with Work IQ Teams to the current conversation. Do not expose reasoning, document contents, operation IDs, or private paths.

## Locked original

The original remains the default because same-item publishing preserves its sharing URL, comments, mentions, tracked changes, version history, and collaboration UX.

When an upload returns `status=locked`, the validated edit is retained privately for the returned bounded expiry:

1. Tell the user that the edit is ready but Microsoft 365 is locking the original. Suggest closing the document in Teams and Word, but do not imply that this guarantees immediate release. WOPI/coauthoring locks automatically expire after 30 minutes and clients or services can refresh that timer. Teams preview, Word Online, synchronization, or post-upload processing can therefore keep a file locked even when no desktop application is open. Keep the operation ID and private path internal.
2. Call `retry_pending_office_publish` with the private `operationId` up to three times. Each call performs only bounded upload retries. Stable Word text-node patches are reapplied and revalidated against a newer source ETag; generic edits fail closed when the source changed.
3. Send at most one concise progress update per minute. Do not rerun the complete edit while a pending operation exists.
4. If the lock persists, explain the two outcomes but do not require the user to type a command. The bridge renders a predefined Adaptive Card:
   - keep retrying the shared original in the background for up to 24 hours, then share a copy automatically;
   - send an explicitly shared editable copy now.
5. For a copy, call `publish_pending_office_copy` only after explicit user choice and pass the invoking user's Entra ID as `recipient_identifier`. The tool must confirm a user-specific write permission before returning the Agent User drive item. Then send that URL through Work IQ Teams. `SendFileToUser(fileUrl=...)` does not grant access to an existing Agent User file. Explain that the copy has a separate version/comment history. If the original changed after a generic edit, also explain that the copy is based on the earlier source.
6. For cancellation, call `cancel_pending_office_publish`.
7. Before repeating an edit after `status=source_changed`, cancel the pending operation so its older bytes cannot be reused accidentally.
8. If a later turn crosses a native transcript rotation and no longer contains the operation ID, call `find_pending_office_publishes` with the same private conversation scope. Use the matching pending operation without exposing its ID.

Never use Graph checkout as lock recovery: checkout intentionally blocks coauthors. Upload sessions improve large-transfer reliability but do not bypass an Office lock.

A Graph `423` with code `notAllowed` and message `The resource you are attempting to access is locked` does not identify the WOPI lock ID or holder. Report the sanitized request ID for support correlation and state that the holder is unavailable; do not guess a person.

Publish body changes before adding comments or mentions because comment activity can change document metadata and ETags.

## Word

Use `minimax_docx_replace_text`, `minimax_docx_insert_paragraph`, or `minimax_docx_fill_placeholders` for ordinary existing-file edits. Use `word_append_tracked` or `word_replace_tracked` only when the user explicitly requires Word tracked changes; these narrow adapters cover operations not exposed by the current MiniMax CLI. All tools execute reviewed Microsoft Open XML SDK code without arbitrary shell access.

For forms and other documents without named placeholders, first use `m365-collaboration.inspect_word_structure`. It returns stable paragraph and text-node indexes plus exact source text. Then call `m365-collaboration.patch_word_text` with typed edits containing `paragraphIndex`, `textNodeIndex`, `searchText`, and `replacementText`. The patch tool enforces the expected source text, verifies the requested replacements, validates, publishes the same item, and cleans up. This is the universal fallback for literal blanks, labels, or text embedded inside ordinary Word runs. Do not infer selectors from escaped Markdown and do not fall back to creating a new document or asking for an email address when the original is editable.

Both commands preserve the original package and generate Word tracked-change markup. Exact replacement intentionally fails unless one text run matches; never guess across split runs. Use Work IQ Word rather than local XML for comments and replies.

## Excel

Prefer `m365-collaboration.read_excel_range` and `write_excel_range` for live workbook collaboration because Microsoft Graph uses the Excel engine and preserves workbook identity. Use local `openpyxl` only for transformations unavailable through Graph, then validate and upload with the ETag workflow.

## PowerPoint

Read with `read_local_office_file`. For a narrow exact text change, use `m365-collaboration.powerpoint_replace_text`.

The command fails unless exactly one drawing text run matches. PowerPoint has no tracked-changes model. Validate and visually inspect every changed presentation.

## PDF

Read with MarkItDown or `pdftotext`. Use `pypdf` for merge, split, rotation, forms, and metadata; use `reportlab` for new PDFs. PDF has no tracked-changes model. Never claim arbitrary text reflow preserves layout without visual verification.
