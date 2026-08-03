# Microsoft Foundry Technical Workshop

A vendor-neutral, one-day technical workshop for professional builders. The material
is customer-agnostic by design: the storyline uses a synthetic retail stock-triage
agent, and any customer name, domain or scenario is chosen during delivery preparation.

## Attendee material

- [Workshop site](docs/index.html)
- [Chapter 2 lab: Build an agent and prove its guardrail](docs/guides/chapter-2-build-agent.html)

## Delivery material

- [Agenda](AGENDA.md)
- [Workshop plan](PLAN.md)
- [Chapter 2 teacher demo](teacher/demos/build-host-agent/OPERATOR.md)
- [Chapter 2 scored model harness](teacher/demos/build-host-agent/harness/)

## Engineering

- [Project instructions](AGENTS.md)
- [Architecture decisions](docs/adr/)
- [ADR template](templates/adr-template.md)

Foundry resources created by this workshop use the `foundryws-` prefix.

## Local preview

Attendee pages are static and have no runtime dependency on a server. Open any
file in `docs/` directly in a browser, or serve this project folder if you want
absolute paths to resolve:

```powershell
uv run python -m http.server 4173
```

Then open `http://127.0.0.1:4173/docs/`.

## Checks

```powershell
uv run pytest
```

The suite validates the attendee HTML against the visual and editorial standards in
`AGENTS.md`, and renders every page in a browser.

## Known gaps

- Only Chapter 2 has an implemented demo and lab. Chapters 1 and 3 to 5 exist as plan
  only.
- The portal screenshots under `docs/assets/screenshots/` were captured before the
  material was generalized and still show customer-specific resource names. Regenerate
  them from a `foundryws-` environment.
- The Chapter 2 evidence quoted in `docs/adr/` predates the neutral advice-boundary
  contract and must be re-measured before it is presented as current.
