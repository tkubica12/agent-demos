# Repository Guidance

## Change Approach

- Treat this as a demonstration and experimentation repository, not a production system.
- Prefer the simplest real implementation that makes architecture, behavior, identity, evaluation, observability, and cost easy to explain and inspect.
- Keep the repository clean and current. Do not preserve backward compatibility, migration paths, archives, duplicated implementations, or superseded attempts.
- Breaking changes are expected. Delete obsolete code, resources, fallbacks, generated files, and documentation once their useful behavior is covered by a current demo.
- Prefer a clean final architecture over compatibility with an older demo.
- Do not add fake demonstrations, mocks, simulations, replay paths, or substantial strategy changes without consulting the user.
- Prefer real product, model, identity, deployment, evaluation, and observability flows whenever practical.
- Prefer scripts over portal instructions. Keep repeatable setup, deployment, validation, and cleanup automated.
- Do not add editorial code comments. Use descriptive names, docstrings where useful, and comments only when logic is otherwise difficult to understand.

## Progressive Demo Rules

- Do not add Hermes, OpenClaw, general-purpose autonomous runtimes, m:n group-chat behavior, replica swarms, or distributed skill learning.
- Keep multi-agent behavior bounded: one primary agent may call focused helpers through platform-native A2A.
- Every numbered step must remain independently understandable and runnable.
- Start a new step by copying the previous step, then change only what the new step needs.
- Keep the primary agent intentionally small and domain-focused.
- Use Foundry-managed capabilities before building custom substitutes.
- Keep gateways and UI adapters thin; agent behavior, tools, memory, skills, and orchestration belong with the hosted agent unless the platform requires otherwise.
- Every step needs the smallest useful unit tests, a local smoke path, a deployed smoke path when supported, and evaluations when behavior quality matters.
- Each step README should state what the step proves, architecture, prerequisites, run and deploy commands, validation, known gaps, and the next added capability.

## Python

- Use Python unless a platform scaffold requires another language.
- Manage Python and dependencies with `uv`.
- Use TOML configuration files.
- Run Python tools, tests, scripts, and applications with `uv run`.
- Commit `uv.lock` whenever dependencies are introduced or changed.

## Terraform and Azure

- Use Terraform with the `azapi` provider for Azure infrastructure.
- Keep Terraform in thematic files such as `providers.tf`, `variables.tf`, `locals.tf`, `identity.tf`, `networking.tf`, `foundry.tf`, and `container_apps.tf`.
- Do not create modules without concrete reuse in this repository.
- Do not use `local-exec`, repeated applies with different variables, or similar procedural workarounds.
- When provisioning requires imperative work, split infrastructure into clear Terraform root layers and run the imperative step explicitly between them.
- Prefer native managed platform features over custom relays and compatibility layers.
- Use managed identities, workload identity, OAuth, and Microsoft Entra authentication for Azure and Microsoft 365 integrations. Do not use stored keys or application secrets.

## Authentication

- Prefer device-code login for Azure and Microsoft 365 authentication.
- When login waits for the user, provide three outcomes: login completed, login has a problem, or the timeout was missed and login should restart.
- Never commit credentials, tokens, tenant-specific secrets, Terraform state, or generated environment files.

## Documentation and Decisions

- Keep the root `README.md` short: state the purpose and link to projects and `docs\`.
- Keep detailed design and operations inside the project they describe.
- Keep educational agent material under `docs\`.
- Do not create documentation files without user agreement.
- Use a focused `SPEC.md` or `ARCHITECTURE.md` when detail is warranted.
- Record architecture decisions in `adr\` only after multiple viable options were discussed and a decision was made. State the options, decision, rationale, and rejected alternatives.
- Do not create ADRs for obvious implementation choices.
- Ask before modifying this file or creating or changing reusable agent skills.

## Completion Standard

- A demo is complete only when its primary no-argument path provides the best classroom experience.
- Validate the real local and deployed behavior represented by the change.
- Keep failures explicit; do not hide unsupported platform behavior behind success-shaped fallbacks.
- Remove temporary resources and superseded implementation paths when they are no longer needed.
