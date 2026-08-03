# Project instructions

These instructions apply to `foundry-1day-workshop/`. They extend the repository
instructions at the root of `agent-demos`.

## Purpose

Build a state-of-the-art technical workshop for professional builders. Balance:

- high-impact teacher demonstrations that show the art of the possible;
- guided hands-on work that creates understanding, confidence, and recall;
- architecture discussion that connects each experience to production use.

Optimize for attendee learning and delivery reliability, not content volume.

## Learning design

Use the workshop rhythm established in `AGENDA.md`:

1. **See it:** Demonstrate a polished end-to-end outcome, including valuable capabilities that are too slow, fragile, or advanced to build live.
2. **Work with it:** Guide one meaningful hands-on outcome in a prepared environment.
3. **Connect it:** Explain the architecture, tradeoffs, operations, governance, and practical adoption path.

Apply these principles:

- Put the strongest strategic message and broadest platform value before the first break.
- Keep the day as one progressive builder journey rather than disconnected features.
- Prefer depth on critical outcomes over shallow coverage.
- Use demonstrations for breadth and labs for durable understanding.
- Design labs to fit their timebox with recovery margin. Move optional depth into explicit extensions.
- Pre-provision slow, failure-prone, permission-sensitive, or low-learning-value setup.
- Use synthetic data and attendee-safe resources.
- Give every chapter a clear outcome, narrative purpose, and transition.
- Treat access, latency, quotas, network policy, and cleanup as part of the learning design.
- Keep the material customer-agnostic. Use neutral, synthetic business scenarios and no
  customer name, brand, or regulated domain. Specialization for a named customer happens
  during delivery preparation, not in this project.

## Repository layout

Use this structure as implementation grows, rooted at `foundry-1day-workshop/`:

```text
teacher/
  demos/<demo-slug>/
student/
  labs/<lab-slug>/
docs/
  slides/
  guides/
  assets/
  adr/
templates/
```

- `teacher/demos/`: Self-contained demo source, infrastructure, automation, tests, and operator notes.
- `student/labs/`: Exercise source, starter state, solution state where appropriate, automation, and tests.
- `docs/`: Attendee-facing HTML slides, guides, shared assets, and ADRs.
- `templates/`: Reusable authoring templates.
- `README.md`: Quick navigation only.
- `AGENTS.md`: Project-wide implementation rules.

Keep attendee-facing guides in `docs/`. Link them to the corresponding code under `student/labs/` or `teacher/demos/`.

## Attendee-facing content

HTML is the source format for all attendee-facing material:

- presentations and slide decks;
- lab instructions;
- reference documentation;
- architecture explanations;
- infographics and interactive diagrams.

Do not use Markdown as the primary attendee experience.

### Visual system

- Support light and dark modes. Respect `prefers-color-scheme` and provide a persistent manual toggle.
- Use black, white, and grayscale plus Microsoft blue as the only accent family. Default accent: `#0078D4`; shades are allowed.
- Keep layouts clean, spacious, restrained, and Microsoft-inspired.
- Use typography, spacing, scale, and motion intentionally. Avoid decorative clutter.
- Do not use Unicode emojis.
- Meet accessible contrast, keyboard navigation, visible focus, semantic HTML, and reduced-motion expectations.
- Make content responsive, but optimize presentation slides for a full-screen 16:9 viewport.
- Keep all runtime dependencies self-contained or vendored in the repository. Do not depend on a CDN or internet-hosted asset at runtime.
- Prefer plain HTML, CSS, and JavaScript. Use React only when component state or interaction complexity clearly justifies its build and maintenance cost.

### Slides

- Slides are visual speaking aids, not documents.
- Follow the 5x5 principle where practical: about five lines with about five words each.
- Use one main idea per slide and progressive disclosure for complexity.
- Provide full-screen presentation mode, keyboard and click navigation, touch controls, slide numbers or progress, and stable deep links.
- Support at least Arrow keys, Space, Page Up/Page Down, Home, End, and `F` for full screen.
- Keep navigation controls unobtrusive during presentation.
- Link relevant slides directly to stable sections in the detailed HTML guide.
- When the audience must capture a reference, show a short readable path or generated QR code in addition to the hyperlink.
- Rehearse the deck in the target browser and resolution. No clipped content, scrollbars, broken media, or illegible code is acceptable.

### Guides and reference documentation

- Address the reader directly. Use imperative steps such as "Open", "Run", and "Verify".
- Never write "students will", "the attendee should", or similar third-person classroom narration.
- Use progressive disclosure: concise core path, expandable explanations, troubleshooting, and optional depth.
- Include stable heading anchors, table-of-contents navigation, cross-links, copyable commands, and expected results.
- Use rich HTML and JavaScript for diagrams, infographics, simulations, and comparisons when this improves understanding.
- Store screenshots and generated artifacts as repository files. Do not embed fragile external references.
- Give meaningful images alt text and complex visuals an equivalent explanation.
- Use Playwright for repeatable screenshots and UI journey checks when the task has a visual interface.

### Editorial state

Everything committed must read as attendee-ready material.

- Do not add editorial comments such as "here I changed", "TODO for later", or "this section was updated".
- Do not expose drafting history or implementation commentary to attendees.
- Do not leave placeholder claims, fake results, unexplained omissions, or broken paths.
- Keep product claims current and evidence-based. Prefer authoritative first-party sources and record the relevant date or version when behavior is time-sensitive.

## Markdown policy

- Keep `README.md` as concise navigation.
- Keep operational Markdown for agents and deep technical readers terse and direct.
- Do not duplicate detailed attendee content in Markdown.
- ADRs are the exception: preserve enough context and reasoning for future maintainers.

## Architecture decision records

Create an ADR for a significant decision about architecture, delivery tooling, content platform, infrastructure, security, or repository organization.

- Store ADRs in `docs/adr/`.
- Name them `NNNN-kebab-case-title.md`.
- Start from `templates/adr-template.md`.
- Record context, decision drivers, evaluated options, chosen option, consequences, and validation.
- State assumptions and explicit revisit triggers: conditions that could make the decision invalid.
- Do not reopen an accepted decision unless its assumptions or conditions changed.
- Mark superseded decisions and link both records; do not erase history.
- Do not create ADRs for routine implementation details with no meaningful alternative.

## Technology and dependency freshness

- Verify volatile service behavior against current authoritative first-party documentation before changing Foundry APIs, SDKs, models, authentication, quotas, limits, or deployment behavior.
- Prefer current stable, supported versions and pin deploy-time tooling and dependencies deliberately.
- Review relevant release notes and run targeted regression checks when upgrading.
- Do not copy stale API versions, sample constraints, or preview behavior into the implementation without verifying that they still apply.

## Automation standards

Automation that deploys, verifies, resets, or cleans up an environment must:

- be idempotent or provide an explicit, safe retry and recovery path;
- support non-interactive execution where practical;
- return a nonzero exit code for partial or complete failure;
- use bounded retries and timeouts and preserve actionable diagnostics, including provider correlation IDs when available;
- report the exact operation scope, failed steps, and any resources left for manual follow-up.

## Teacher demonstrations

Every teacher demo must be:

- self-contained and isolated from unrelated demos;
- deployable from a clean, documented starting point;
- automated, repeatable, and idempotent where the platform permits;
- presentable without manual repair or hidden setup;
- safe to run with synthetic data and no committed secrets;
- designed with a fast reset, cleanup path, and documented fallback.

Each demo must provide:

- prerequisites and a preflight check;
- non-interactive deploy, verify, and cleanup automation under `scripts/`;
- an operator flow with timing, talking points, expected states, and transition cues;
- automated deployment and smoke tests;
- a scripted test of the exact showcase path;
- recovery instructions for likely failures;
- an explicit supported platform if automation is not cross-platform.

Test the demo from scratch, not only against an existing environment. Verify the visible result, not only deployment success.

## Student labs

Design each lab around one meaningful outcome and a reliable path to completion.

Each lab guide must include:

- the outcome and estimated time;
- prerequisites and access preflight;
- prepared starting state;
- numbered, copyable steps;
- expected output and checkpoints;
- concise explanations at the moment they are useful;
- troubleshooting tied to observable symptoms;
- reset, resume, and cleanup paths;
- optional extensions separated from the core path.

Automation must prepare accounts, permissions, resources, data, and starter files wherever practical. Make the access path explicit: URL, authentication method, expected tenant or subscription, and resource naming.

Test every command exactly as written from the documented starting state. Do not rely on author machine state, cached credentials, undeclared tools, or implicit permissions.

## Validation

Use the smallest reliable automated checks that cover the changed experience. Relevant checks include:

- HTML validation, link checking, and accessibility checks;
- responsive and light/dark visual checks;
- keyboard-only slide navigation;
- Playwright journeys and screenshot comparison;
- clean deployment, smoke, showcase-path, repeat-deployment, reset, and cleanup tests;
- clean-room execution of lab instructions;
- secret scanning and verification that synthetic data is used.

Do not call work complete because code builds. Do not claim a live or attendee-ready outcome from mocks, plans, successful deployments, or API responses alone. Verify the exact attendee-visible journey and the delivery path.

## Required review gates

Run independent sub-agent reviews at relevant deliverable boundaries. Give reviewers the goal, audience, agenda context, changed files, and exact validation instructions. Reviewers must inspect the actual artifact and evidence, not only a summary.

### Educator review

Required for every learner-facing content change.

Ask an Educator agent to assess:

- learning objective alignment;
- clarity, pacing, cognitive load, and chapter flow;
- engagement and quality of demonstrations;
- balance of demonstration, hands-on work, and architecture discussion;
- suitability for the target professional-builder audience;
- whether the material is genuinely presentable and useful.

### Student test

Required for every new or changed lab.

Ask a Student tester agent with fresh context to:

- start from the documented prerequisites;
- follow only the attendee guide;
- execute every step and checkpoint;
- report ambiguity, missing access, hidden assumptions, errors, timing risk, and recovery gaps;
- assess whether the result is understandable and satisfying.

### Teacher test

Required for every new or changed demonstration.

Ask a Teacher tester agent with fresh context to:

- deploy the demo from scratch using only documented automation;
- run the exact presentation path;
- verify the visible result and cleanup;
- review slides, detailed guide, timing, talking points, transitions, and fallback;
- report reliability, presentation, and lecture-flow issues.

### Clean-room reproducibility review

Required for every new or changed deployment, environment preparation, reset, or cleanup workflow.

Ask an independent reviewer with fresh context to:

- start from the documented prerequisites and a clean environment;
- use only the documented automation and operator guidance;
- identify undeclared tools, ambient state, cached credentials, hidden manual steps, and non-idempotent behavior;
- verify retry, recovery, repeat-deployment, reset, and cleanup behavior;
- report any difference between the documented and observed outcome.

### Security and destructive-safety review

Required for changes involving authentication, permissions, secrets, cloud resources, deployment, or cleanup.

Ask an independent reviewer to assess:

- secret handling across source, configuration, state, plans, logs, generated artifacts, and error messages;
- least privilege, access boundaries, and use of synthetic data;
- destructive scope, ownership evidence, confirmations, and recovery options;
- whether retries, partial failures, or stale state could broaden the affected scope;
- whether cleanup reports retained resources and required manual follow-up.

Use a role-prompted general-purpose sub-agent when a named specialist is unavailable. Iterate on all blocking and material findings. Repeat the relevant review until the reviewer reports no blocking or material issue. Record concise review evidence in the change or pull request, not in attendee-facing content.

## Definition of done

A deliverable is complete only when:

- it follows the repository structure and attendee-facing HTML policy;
- it is accurate, polished, accessible, cross-linked, and free of editorial residue;
- its automation and exact user journey pass from a clean starting state;
- relevant screenshots and artifacts are current;
- secrets and real customer data are absent;
- significant decisions have an ADR;
- every relevant sub-agent review gate is satisfied with no blocking or material findings.
