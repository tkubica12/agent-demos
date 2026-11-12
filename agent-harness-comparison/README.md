# Four harnesses, one trusted research application

Compare **LangChain Deep Agents**, **Microsoft Agent Framework Harness**,
**GitHub Copilot SDK**, and **Pi** without replacing their native agent loops.
The application researches a technical decision, inspects WebIQ passages,
repairs a structured draft, and delivers real offline HTML and Chromium PDF.

The comparison document is generated at
[`report\comparison.html`](report/comparison.html). Its transferable export is
`report\comparison.standalone.html`; the requested six-page paper is
`report\comparison.paper.pdf`. An incomplete campaign is prominently labeled
incomplete. See [`GOAL.md`](GOAL.md) for the acceptance contract.

**Recorded campaign: 144 attempts and 144 blinded judgments.** The original
32 four-worker attempts remain separate quota-stress diagnostics. The main
comparison uses 112 serial attempts, 28 per variant: 106 successful deliveries
and six failures. No attempt was replaced. The exact no-argument classroom path
subsequently delivered HTML/PDF through all four variants.

| Main serial cohort | Delivered | Median success | Quality including failures / 4 | Adapter LOC |
|---|---|---|---|---|
| Deep Agents | 25/28 | 72.3 s | 3.41 | 50 |
| Agent Framework | 27/28 | 83.0 s | 3.69 | 36 |
| Copilot SDK | 27/28 | 62.5 s | 3.61 | 89 |
| Pi | 27/28 | 62.7 s | 3.66 | 149 |

These are local, profile-specific observations, not a universal ranking.
The existing deployment allows 100,000 tokens/minute and 100 requests/minute;
confirmed quota errors affected the initial cohort. DNS errors and two host
hibernations are also preserved and annotated, not attributed automatically
to SDK quality. Successful-brief content scores are shown separately from
failure-zero scores in the HTML.

The unchanged cumulative limits are USD 200 and 240 operations. The final
ledger contains 214 operations including debugging, 36 evaluator batches,
and four default-path smoke runs; its conservative envelope is USD 125.40,
including unknown-usage reservations, **not billed spend**.

## Architecture

The Python host owns credentials, the WebIQ connection, a persistent budget,
five narrow tools, report validation, HTML escaping, and publication. Each
adapter supplies those tools to its **native** harness:

| Adapter | Native execution | Process boundary |
|---|---|---|
| Deep Agents | `create_deep_agent`, LangGraph, TODO middleware | Python |
| Agent Framework | `create_harness_agent`, native providers | Python |
| Copilot SDK | An isolated session in the installed Copilot runtime | Python / stdio runtime |
| Pi | `createAgentSession`, explicit resources and custom tools | Python / JSONL / Node |

The agent chooses searches, follow-up reading, and repairs. The host only
exposes `search_web`, `inspect_source`, `save_draft`, `validate_report`, and
`publish_report`. Publication checks the schema, exact quotes from inspected
passages, at least three sources across two domains, and length. It never
accepts arbitrary HTML, shell commands, or output paths.

This is a capability boundary, **not an OS sandbox or a guarantee of factual
correctness**. Exact quotations establish traceability, not entailment. The
blinded evaluator separately judges source support.

## Prerequisites and setup

This experiment was exercised on Windows with Python 3.12.9, Node 24.17.0,
`uv`, Azure CLI, Microsoft Edge, and an already installed approved Copilot
runtime. Python and npm versions are locked. No new Azure resources, API keys
for Foundry, application secrets, or deployment scripts are needed.

The existing Foundry deployment and retail pricing assumptions are in
`config.toml`. The model is `gpt-6-1-sol` / `gpt-6.1-sol`, version `2026-09-29`,
Global Standard, Responses API, reasoning `low`. The app authenticates through
the current Azure CLI Entra identity and refreshes an expiring token; it does
not store that token. Run device-code login if the existing login is absent.

```powershell
Set-Location D:\agent-demos\agent-harness-comparison
az login --use-device-code
uv sync --frozen
npm ci --ignore-scripts --no-audit --no-fund
```

Both project registries use the approved Microsoft package feed. Do not replace
them with public registries. The MCP client is pinned to `1.30.0`; the pilot
encountered generic server errors with SDK 2.x and later observed explicit
WebIQ HTTP 503 responses. Those observations do not by themselves establish
a single server root cause.

Enter the supplied WebIQ key without echoing it or writing an environment file:

```powershell
$secret = Read-Host "WebIQ API key" -AsSecureString
$env:WEBIQ_API_KEY = [Net.NetworkCredential]::new("", $secret).Password
```

The Copilot app already supplies `COPILOT_CLI_RESOLVED_DIST_DIR`. From another
terminal, set it to the existing approved distribution directory containing
`prebuilds\win32-x64\copilot-runtime.exe`. The measured runtime was `1.0.99-2`.
The demo deliberately does not download a CLI or use the logged-in Copilot
account for model access.

## Run

The no-argument path runs the same default decision brief through all four
native variants and prints delivery paths and metrics after each:

```powershell
uv run harness-lab
```

Each attempt creates `runs\<run-id>\report.standalone.html` and `report.pdf`
only after validation succeeds. A failed variant remains a recorded failure;
the all-variant demo returns a nonzero exit status if any variant fails.

```powershell
uv run harness-lab smoke --variant deepagents --brief 0
uv run harness-lab smoke --variant maf --brief 4
uv run harness-lab smoke --variant copilot --brief 0
uv run harness-lab smoke --variant pi --brief 0
uv run python -m harness_lab.preflight
```

All research and model calls are real. The decision briefs are explicitly
hypothetical. This WebIQ account exposes `web`, `images`, and `videos`, but not
`browse`; the app uses `web` and preserves indexed passages, not full webpages.

## Repeatable measurements and evaluation

```powershell
uv run harness-lab benchmark
uv run harness-lab evaluate
uv run harness-lab report
uv run python -m harness_lab.verify
```

The benchmark freezes workload, rubric, model configuration, source cutoff,
source code, and lockfile hashes in `runs\experiment.json`. It records
12 briefs x 3 repetitions x 4 variants = **144 attempts**, including failures.
A seeded, paired queue runs one job at a time, with a persisted 60-second
cooldown after completion. The first eight groups of the current campaign
were already attempted with four workers; the remaining 28 whole groups run
serially. No group crosses the phase boundary. Resume the same command to run
only pending cells. A previously started but interrupted attempt is retained,
not silently replaced. Changing frozen inputs prevents resume. The explicitly
approved scheduler revision preserves the previous fingerprint and verifies
that every other frozen file is unchanged. The report never pools the two
phases into a performance ranking.

Evaluation uses 36 same-task groups of four anonymous, shuffled outputs.
Evaluator requests also run serially with a 60-second cooldown.
It scores decision usefulness, source entailment, counterevidence, uncertainty,
and compliance on a frozen 0-4 scale. Failed attempts receive zero. Author and
judge use the same Sol deployment; this is not independent human validation.
The evaluator's initial API/schema probe is excluded from comparison results;
its mixed-task inputs test transport and schema, not comparative quality.

`report` also independently rechecks each successful artifact's schema,
citations, PDF header/page count, and hashes. It writes safe repeatable
aggregates under `results\`. When raw runs are absent, the committed safe
summary can still regenerate the document offline. Partial results are valid
diagnostic artifacts, but `report` returns exit status 1 until all 144 attempts
and 144 judgments are accounted for.

Keep `runs\state.sqlite3` and the raw experiment together. Do not delete failed
attempts or the ledger to obtain a more favorable score or reset the budget.
Starting a separate campaign requires an explicit new budget and experiment
workspace; this project does not silently reset campaigns.

### Metric definitions and limits

- End-to-end latency includes setup, tools, export, and native completion.
  `harness_ready` measures configuration, not time to first token. The agent
  interval includes tool/export time; these intervals are not additive.
  Deliberate scheduler cooldown is outside per-attempt latency.
- Normalized input includes uncached input, cache reads, and cache writes.
  Pi reports the uncached part separately; MAF and the other SDKs expose
  different field names. Raw native counters are retained.
- Price is an estimated short-context Global Standard retail calculation,
  not an Azure invoice: USD 2 / 0.10 / 2.50 / 10 per million uncached input /
  cache read / cache write / output tokens. WebIQ USD 0.007 per call is the
  owner's estimate, not a verified public tariff.
- Missing or partial failed-request usage is not a zero bill. Failed runs
  retain a USD 3.25 reservation. SDK error placeholders with zero counters
  are not evidence of zero provider charges.
- Per job: 6 searches, 5 distinct source reads, 18 common tool executions,
  24 intended model responses/iterations, and a 360-second execution deadline.
  Model stopping differs by native SDK; Node response observers are not a
  hard pre-request quota. Rejected tool attempts can increase the attempted
  call counter beyond 18 without executing another tool.
- Total ledger limits: 240 operations and estimated USD 200, with USD 20
  reserved from benchmark allocation; WebIQ at most 10,000 calls. A measured
  campaign has the owner's revised sixteen-hour total task window. The
  original window ended at 2026-10-23 01:23 UTC; the newly approved awake-host
  window ends at 2026-10-23 09:13 UTC. Both remain in the experiment history. The scheduler
  reserves fifteen minutes for final verification and rechecks the deadline
  after sleeping. Cleanup may follow the execution timeout.
- WebIQ has one logged retry for HTTP 429/502/503/504, with another debit for
  an actually repeated search call. No failed measured cell is replaced.
  Native model transport/retry behavior is not perfectly identical.

Shared network, Foundry quota, CPU, model cache, runtime prompts, and package
import warming remain confounders. Report successful latency and cost together
with failure rate, usage coverage, and quality including failures.
The serial subset has identical task/repetition coverage for all four variants,
but individual briefs need not have equal representation within that subset.

## Validation

```powershell
uv run python -m unittest discover -s tests -q
uv run python -m harness_lab.verify
```

The unit fixtures exercise deterministic contracts only; they are not used
as substitutes for real research/model runs. Integration evidence lives in
the recorded smoke runs and the full campaign.

Canonical html-docs validation can reuse Playwright already installed with
the Python dependency, avoiding a separate npm/browser download:

```powershell
$env:PLAYWRIGHT_MODULE = (Resolve-Path ".venv\Lib\site-packages\playwright\driver\package").Path
$env:PLAYWRIGHT_CHROMIUM = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
node report\assets\validate.js report\comparison.standalone.html --viewport 1440x900
node report\assets\validate.js report\comparison.standalone.html --viewport 1920x1080
```

This checks all eight theme/accent combinations, print, offline operation,
JavaScript-disabled reading, and the six fixed paper pages. `report` bundles
the single-file HTML and exports the six-page PDF automatically. The runtime
assets remain unmodified and retain their MIT notice. There are Read and
Sheet views, not Slides.

## Scope and next capability

Application execution is local only; **there is no deployed application
smoke path**. The Foundry model and WebIQ are existing live services.
Multi-agent delegation, long-context stress tests, filesystem skills,
conversation recovery, production security hardening, and full-page browsing
are not demonstrated. A useful next experiment would isolate native
compaction or human publication approval rather than adding a general-purpose
autonomous runtime.

After the session, remove the key from this terminal's environment:

```powershell
Remove-Item Env:\WEBIQ_API_KEY
```
