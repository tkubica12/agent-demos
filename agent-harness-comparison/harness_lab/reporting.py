from __future__ import annotations

import ast
import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from .core import ROOT, VARIANTS, Budget, Report, ResearchRun, config, escape, write_json
from .experiment import CRITERIA, STATE
from .publishing import bundle, card, document, print_pdf

NAMES = {
    "deepagents": "LangChain Deep Agents", "maf": "Microsoft Agent Framework",
    "copilot": "GitHub Copilot SDK", "pi": "Pi",
}

SOURCES = [
    ("D", "Deep Agents overview", "https://docs.langchain.com/oss/python/deepagents/overview",
     "Načteno; část rozsáhlé stránky zkrácena. Primitives a harness profiles; konkrétní API ověřeno i v instalovaném balíčku."),
    ("M", "Microsoft Agent Framework harness", "https://learn.microsoft.com/en-us/agent-framework/concepts/harness?pivots=programming-language-python",
     "Načteno celé. Factory, providers, compaction; rozlišení released, experimental a prerelease funkcí."),
    ("C", "Copilot SDK BYOK", "https://raw.githubusercontent.com/github/copilot-sdk/main/docs/auth/byok.md",
     "Načteno. Responses a bearer-token provider; obecný README měl užší, zastaralé tvrzení o API keys."),
    ("CI", "Copilot SDK managed identity", "https://raw.githubusercontent.com/github/copilot-sdk/main/docs/setup/azure-managed-identity.md",
     "Načteno. Entra callback; v demu se používá lokální Azure CLI identita, ne nasazená managed identity."),
    ("P", "Pi SDK", "https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/docs/sdk.md",
     "Načteno. ModelRuntime, custom tools, in-memory sessions a explicitní resource loader."),
    ("PI", "Pi", "https://pi.dev/", "Načteno. Kontext produktu; implementace vychází z SDK dokumentace a typů."),
    ("W", "WebIQ MCP integration", "https://learn.microsoft.com/en-us/web-iq/integrations/mcp",
     "Načteno celé. Account-scoped discovery a autentizace; reálný účet exponoval web, images, videos, nikoli browse."),
    ("S", "Sol model documentation", "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
     "Načteno. Responses pro nástroje, reasoning low a tokenové sazby."),
    ("A", "Azure OpenAI pricing", "https://azure.microsoft.com/en-us/pricing/details/azure-openai/",
     "Přímé načtení timeout; sazby Global Standard ověřeny přes indexovanou WebIQ pasáž této primární stránky."),
    ("MS", "MAF streaming example", "https://learn.microsoft.com/agent-framework/agents/structured-outputs?pivots=programming-language-python",
     "Oficiální code-sample search: stream=True a get_final_response()."),
    ("CP", "Copilot Python SDK", "https://raw.githubusercontent.com/github/copilot-sdk/main/python/README.md",
     "Načteno se zkrácením; nativní typy a eventy ověřeny v instalovaném SDK."),
    ("CR", "Copilot SDK repository", "https://github.com/github/copilot-sdk",
     "Načten obecný přehled; pro Entra má přednost konkrétní BYOK dokumentace a skutečný průchod."),
]

PROFILES = {
    "deepagents": {
        "title": "Deep Agents přidává harness do Python aplikace",
        "wiring": "ChatOpenAI používá Responses se streamováním a Entra tokenem. create_deep_agent sestaví nativní LangGraph smyčku; společné nástroje jsou StructuredTool.",
        "strength": "Výslovné middleware a harness profiles umožňují vysvětlit plánování, práci s kontextem a omezení nástrojů na jednom místě. TODO přidává nativní TodoListMiddleware.",
        "cost": "Přichází širší middleware stack a jeho instrukce. Graph recursion limit není totéž co počet modelových volání; příliš nízký limit v pilotu zastavil i validní rozpracovaný report.",
        "scope": "Profil vylučuje filesystem, shell a task/subagents. Využíváme nativní agentní smyčku a dostupné TODO; dlouhý kontext, checkpoint recovery ani delegaci tento benchmark neověřuje.",
        "primitive": "StructuredTool + middleware", "planning": "TODO middleware",
        "auth": "ChatOpenAI + token", "usage": "LangChain usage_metadata", "citation": "D",
        "code": "model = ChatOpenAI(..., use_responses_api=True)\nagent = create_deep_agent(\n    model=model, tools=research_tools,\n    middleware=[TodoListMiddleware()],\n)\nresult = await agent.ainvoke(messages, config=limits)",
    },
    "maf": {
        "title": "Agent Framework skládá harness z providers",
        "wiring": "OpenAIChatClient z agent-framework-openai obaluje Responses klienta. create_harness_agent přidá nativní providers; agent.run vrátí finální AgentResponse a agregovanou usage.",
        "strength": "Agent, tools, sessions a harness zůstávají v jednom Python modelu. Nativní function invocation configuration a agregovaná usage se snadno propojí s hostitelskou aplikací.",
        "cost": "Verze core a connectoru musí souhlasit. Výchozí plan mode vyžadoval schválení člověkem; unattended úloha jej musí vědomě vypnout, nikoli čekání vydávat za dokončený běh.",
        "scope": "Používáme disable_mode=True, bez file memory a bez vlastního web search provideru. TODO provider zůstává nativní. Filesystem, background work a další experimentální providers nejsou naměřeným důkazem.",
        "primitive": "@tool + harness providers", "planning": "Nativní TODO provider",
        "auth": "AsyncOpenAI + callback", "usage": "AgentResponse.usage_details", "citation": "M",
        "code": "agent = create_harness_agent(\n    client=client, tools=research_tools,\n    disable_mode=True,\n    disable_file_memory=True,\n    disable_web_search=True,\n)\nresponse = await agent.run(prompt, session=session)",
    },
    "copilot": {
        "title": "Copilot SDK dělá z runtime součást aplikace",
        "wiring": "Python host spustí existující schválený Copilot runtime přes stdio. Session používá BYOK Responses, bearer_token_provider a pět aplikačních tools, ne GitHub Copilot předplatné pro modelové účtování.",
        "strength": "Aplikace integruje hotovou agentní exekuci, session události a permission boundary. Nemusí implementovat model-tool smyčku ani vlastní správu konverzace.",
        "cost": "Další runtime proces, verzování CLI a Python SDK a explicitní izolace konfigurace. Tool allowlist není OS sandbox. Nativní event obsahuje i vnořené dataclasses; usage se musí bezpečně normalizovat.",
        "scope": "mode=empty, discovery vypnuto, systémové instrukce nahrazeny, host git operations a session store vypnuty. Všechny nativní permission requests jsou odmítnuty; pět úzkých app tools má explicitní povolení.",
        "primitive": "Runtime + session + Tool", "planning": "V tomto profilu v konverzaci",
        "auth": "bearer_token_provider", "usage": "assistant.usage events", "citation": "C",
        "code": "client = CopilotClient(connection=stdio_runtime, mode=\"empty\")\nsession = await client.create_session(\n    provider=foundry_with_bearer_callback,\n    tools=research_tools,\n    available_tools=research_tool_names,\n    on_permission_request=deny_permission,\n)\nmessage = await session.send_and_wait(prompt)",
    },
    "pi": {
        "title": "Pi ponechává malý core a explicitní rozšíření",
        "wiring": "Nativní Node SDK registruje Foundry jako openai-responses provider a zavolá createAgentSession. Pět custom tools posílá požadavky Python hostu přes úzký JSONL bridge.",
        "strength": "Přímý SDK přístup, vlastní tools, in-memory session a explicitní resource loader. Je vidět, co agent skutečně dostal; není nutné přebírat uživatelovy skills nebo globální konfiguraci.",
        "cost": "V Python aplikaci přibude Node a mezijazykový bridge. Pi input nezahrnuje cacheRead/cacheWrite; bez normalizace by stejné položky vytvářely zavádějící srovnání i zápornou cenu.",
        "scope": "Žádné defaultní shell/file tools, skills ani extensions. Nativní agentní smyčka a compaction zůstávají v SDK; kompakce však v malém workloadu není samostatně stress-testována.",
        "primitive": "createAgentSession + customTools", "planning": "V tomto profilu v konverzaci",
        "auth": "Provider + token v paměti", "usage": "message_end assistant usage", "citation": "P",
        "code": "runtime.registerProvider(\"foundry\", responses_provider)\nconst { session } = await createAgentSession({\n  modelRuntime: runtime, customTools,\n  tools: researchToolNames,\n  sessionManager: SessionManager.inMemory(cwd),\n  resourceLoader: explicitLoader,\n})\nawait session.prompt(prompt)",
    },
}


def median(values):
    return statistics.median(values) if values else None


def number(value, digits=1):
    return "N/A" if value is None else f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def table(headers: list[str], rows: list[list[str]], *, sheet=False) -> str:
    css = ' class="sheet-matrix"' if sheet else ""
    return (
        f"<table{css}><thead><tr>" + "".join(f'<th scope="col">{escape(text)}</th>' for text in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(
            f'<th scope="row">{escape(text)}</th>' if index == 0 else f"<td>{escape(text)}</td>"
            for index, text in enumerate(row)
        ) + "</tr>" for row in rows) + "</tbody></table>"
    )


def code_counts() -> dict:
    source = (ROOT / "harness_lab" / "adapters.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    counts = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] = sum(
                bool(line.strip()) and not line.lstrip().startswith("#")
                for line in lines[node.lineno - 1:node.end_lineno]
            )
    node_lines = sum(bool(line.strip()) for line in (ROOT / "pi.mjs").read_text(encoding="utf-8").splitlines())
    native = {variant: counts[variant] for variant in VARIANTS}
    native["pi"] += node_lines
    common = counts["record_usage"] + sum(
        sum(bool(line.strip()) and not line.lstrip().startswith("#") for line in
            (ROOT / "harness_lab" / name).read_text(encoding="utf-8").splitlines())
        for name in ("core.py", "publishing.py", "cli.py")
    )
    return {"adapters_nonblank_loc": native, "common_application_nonblank_loc": common}


def artifact_check(directory: Path, result: dict) -> dict:
    if result["status"] != "passed":
        return {"passed": False, "reason": "attempt_failed"}
    try:
        run = object.__new__(ResearchRun)
        run.settings = config()
        run.draft = Report.model_validate_json((directory / "draft.json").read_text(encoding="utf-8"))
        run.sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
        run.inspected = set(result["inspected_sources"])
        run.queries = result["queries"]
        issues = run.problems()
        pdf = (directory / "report.pdf").read_bytes()
        html = (directory / "report.standalone.html").read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page\b", pdf))
        if not pdf.startswith(b"%PDF") or pages < 1 or b"data-doc-tokens" not in html:
            issues.append("Missing or malformed delivered artifact.")
        return {
            "passed": not issues, "issues": issues, "pdf_pages": pages,
            "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            "html_sha256": hashlib.sha256(html).hexdigest(),
        }
    except (OSError, ValueError, KeyError) as error:
        return {"passed": False, "reason": str(error)}


def metrics_for_records(records: list[dict]) -> dict:
    metrics = {}
    for variant in VARIANTS:
        selected = [record for record in records if record["variant"] == variant]
        passed = [record for record in selected if record["status"] == "passed"]
        metered = [record for record in passed if record.get("tokens") is not None]
        judged = [record for record in selected if record.get("quality") is not None]
        quality = [record["quality"]["mean"] for record in judged]
        successful_quality = [record["quality"]["mean"] for record in passed if record.get("quality") is not None]
        metrics[variant] = {
            "planned": len(selected),
            "attempts": sum(record["run_id"] is not None for record in selected),
            "passed": len(passed), "successful_usage_coverage": len(metered),
            "median_success_seconds": median([record["elapsed_seconds"] for record in passed]),
            "median_setup_seconds": median([record["setup_seconds"] for record in passed if record.get("setup_seconds") is not None]),
            "median_export_seconds": median([record["export_seconds"] for record in passed]),
            "median_input_tokens": median([record["tokens"]["input_tokens"] for record in metered]),
            "median_output_tokens": median([record["tokens"]["output_tokens"] for record in metered]),
            "mean_success_estimated_usd": statistics.mean([
                record["model_estimated_usd"] + record["webiq_estimated_usd"]
                for record in metered
            ]) if metered else None,
            "quality_mean_including_failures": statistics.mean(quality) if quality else None,
            "quality_mean_success_only": statistics.mean(successful_quality) if successful_quality else None,
            "quality_criteria_means": {
                criterion: statistics.mean(record["quality"]["criteria"][criterion] for record in judged)
                if judged else None for criterion in CRITERIA
            },
            "quality_coverage": len(quality),
            "deterministic_passes": sum(record.get("artifact_check", {}).get("passed", False) for record in selected),
        }
    return metrics


def aggregate() -> dict:
    output = ROOT / "results" / "summary.json"
    if not STATE.exists():
        if output.exists():
            return json.loads(output.read_text(encoding="utf-8"))
        state = {"pairs": [], "python_packages": {}, "fingerprint": {}, "created_at": None}
    else:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    judgments = {}
    for path in (ROOT / "runs" / "evaluations").glob("*.json"):
        evaluation = json.loads(path.read_text(encoding="utf-8"))
        if evaluation["pair_id"] not in {pair["id"] for pair in state["pairs"]}:
            continue
        for verdict in evaluation["judgments"]:
            run_id = evaluation["mapping"][verdict["label"]]["run_id"]
            judgments[run_id] = {
                "criteria": {name: verdict[name] for name in CRITERIA},
                "mean": statistics.mean(verdict[name] for name in CRITERIA),
                "rationale": verdict["rationale"],
            }
    records = []
    for pair in state["pairs"]:
        for cell in pair["cells"]:
            record = {"pair": pair["id"], "brief": pair["brief"]["id"], **cell}
            if cell["run_id"]:
                directory = ROOT / "runs" / cell["run_id"]
                path = directory / "result.json"
                if path.exists():
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    record.update({name: raw.get(name) for name in (
                        "status", "elapsed_seconds", "searches", "research_tool_calls",
                        "model_estimated_usd", "webiq_estimated_usd", "usage_status", "error_type",
                    )})
                    record["tokens"] = {
                        name: sum(item.get(name, 0) for item in raw["usage"])
                        for name in ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens")
                    } if raw["usage"] else None
                    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                    ready = next((event["seconds"] for event in events if event["kind"] == "harness_ready"), None)
                    end = next((event["seconds"] for event in events if event["kind"] == "agent_end"), None)
                    record["setup_seconds"] = ready
                    record["agent_seconds_including_tools_and_export"] = end - ready if ready is not None and end is not None else None
                    record["export_seconds"] = sum(event["export_seconds"] for event in events if event["kind"] == "publication")
                    record["webiq_retries"] = sum(event["kind"] == "webiq_http_retry" for event in events)
                    record["artifact_check"] = artifact_check(directory, raw)
                    record["result_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            record["quality"] = judgments.get(cell["run_id"])
            records.append(record)
    primary_phase = state.get("primary_phase", "serial")
    primary_records = [record for record in records if record.get("measurement_phase", "serial") == primary_phase]
    phases = []
    for protocol in state.get("protocol_history", []):
        selected = [record for record in records if record.get("measurement_phase") == protocol["id"]]
        phases.append({
            "id": protocol["id"], "planned": len(selected),
            "attempts": sum(record["run_id"] is not None for record in selected),
            "quality_coverage": sum(record["quality"] is not None for record in selected),
            "paired_groups": len({record["pair"] for record in selected}),
            "workers": protocol["workers"], "cooldown_seconds": protocol["cooldown_seconds"],
            "statuses": dict(Counter(record["status"] for record in selected)),
            "brief_cells": dict(Counter(record["brief"] for record in selected)),
            "metrics": metrics_for_records(selected),
        })
    budget = Budget()
    with budget.connect() as db:
        operation_count, envelope = db.execute("SELECT COUNT(*),SUM(COALESCE(cost,reserve)) FROM runs").fetchone()
        web_calls, web_cost = db.execute("SELECT COUNT(*),COALESCE(SUM(cost),0) FROM calls").fetchone()
        unresolved = db.execute("SELECT COUNT(*) FROM runs WHERE cost IS NULL AND reserve>0").fetchone()[0]
    attempts = sum(record["run_id"] is not None for record in records)
    covered = sum(record["quality"] is not None for record in records)
    complete = attempts == 144 and covered == 144 and all(record["status"] not in {"pending", "running"} for record in records)
    summary = {
        "status": "complete" if complete else "incomplete", "planned": 144,
        "attempts": attempts, "quality_coverage": covered,
        "primary_phase": primary_phase, "primary_planned": len(primary_records),
        "primary_attempts": sum(record["run_id"] is not None for record in primary_records),
        "metrics": metrics_for_records(primary_records), "phases": phases,
        "protocol_history": state.get("protocol_history", []),
        "records": records, "python_packages": state["python_packages"],
        "frozen_fingerprint": state["fingerprint"], "experiment_created_at": state["created_at"],
        "paused_reason": state.get("paused_reason"),
        "execution_status": state.get("execution_status", "running"),
        "operational_context": state.get("operational_context", []),
        "time_windows": state.get("time_windows", []),
        "experiment_completed_at": state.get("completed_at"),
        "validation_notes": state.get("validation_notes", []),
        "source_cutoff": config()["benchmark"]["source_cutoff"], "pricing": config()["pricing"],
        "runtime_versions": state.get("runtime_versions", {}),
        "operations": operation_count, "webiq_calls": web_calls,
        "conservative_spending_envelope_usd": (envelope or 0) + web_cost,
        "runs_with_retained_unknown_usage_reservation": unresolved,
        "code": code_counts(),
    }
    output.parent.mkdir(exist_ok=True)
    write_json(output, summary)
    write_json(ROOT / "results" / "sources.json", {
        "consulted": [{"id": key, "title": title, "url": url, "status_and_use": note} for key, title, url, note in SOURCES],
        "failed_or_unused": [
            "WebIQ pricing page: timeout; Learn pricing search returned no evidence. WebIQ USD 0.007/call is the owner's estimate.",
            "Guessed MAF _harness.py raw source URL: 404, not used. Actual installed _harness/_agent.py inspected.",
            "Broad Learn search chunks not fully read were not treated as inspected evidence.",
        ],
        "excluded": ["Internal Microsoft 365 sources", "Community sentiment", "Long-context stress testing", "Multi-agent delegation"],
    })
    return summary


def diagram() -> str:
    return """<figure class="sheet-figure"><svg viewBox="0 0 760 180" role="img"
aria-label="Dvě cesty vedou ke společné důvěryhodné aplikaci">
<g fill="var(--surface-2)" stroke="var(--border-strong)" stroke-width="1.5">
<rect x="2" y="3" width="240" height="72" rx="9"/>
<rect x="2" y="102" width="240" height="72" rx="9"/>
<rect x="288" y="38" width="254" height="104" rx="9" stroke="var(--accent)"/>
<rect x="587" y="53" width="170" height="76" rx="9"/></g>
<g fill="none" stroke="var(--accent)" stroke-width="2">
<path d="M243 39H266V75H285M243 138H266V105H285M543 91H584"/>
<path d="m278 68 7 7-7 7m0 16 7 7-7 7m299-28 7 7-7 7"/></g>
<g fill="var(--text)" font-family="Segoe UI, sans-serif" font-size="17" text-anchor="middle">
<text x="122" y="31">Framework → harness</text>
<text x="122" y="57" font-size="14">Deep Agents · Agent Framework</text>
<text x="122" y="130">Harness → aplikace</text>
<text x="122" y="156" font-size="14">Copilot SDK · Pi</text>
<text x="415" y="72">Trusted host</text>
<text x="415" y="99" font-size="14">identity · tools · budget</text>
<text x="415" y="121" font-size="14">validace · publikace</text>
<text x="672" y="85">HTML + PDF</text>
<text x="672" y="109" font-size="14">ověřitelný podklad</text>
</g></svg><figcaption>Agent vybírá další krok; aplikace rozhoduje, co smí vykonat a vydat.</figcaption></figure>"""


def phase_diagram(summary: dict) -> str:
    initial = next((phase for phase in summary["phases"] if phase["id"] == "parallel_quota"), None)
    if initial is None:
        return diagram()
    serial = next(phase for phase in summary["phases"] if phase["id"] == "serial")
    split = round(752 * initial["planned"] / summary["planned"])
    return f"""<figure class="sheet-figure"><svg viewBox="0 0 760 118" role="img"
aria-label="Dvě oddělené fáze stejného plánu 144 pokusů">
<g fill="var(--surface-2)" stroke="var(--border-strong)" stroke-width="1.5">
<rect x="2" y="34" width="{split}" height="38" rx="6"/>
<rect x="{split + 8}" y="34" width="{750 - split}" height="38" rx="6" stroke="var(--accent)"/></g>
<g fill="var(--text)" font-family="Segoe UI, sans-serif" font-size="16" text-anchor="middle">
<text x="{split / 2 + 2}" y="21">4 souběžné úlohy</text>
<text x="{split / 2 + 2}" y="59">{initial['planned']} pokusů</text>
<text x="{split / 2 + 2}" y="97">{initial['paired_groups']} skupin · diagnostika</text>
<text x="{(split + 768) / 2}" y="21">1 úloha + 60 s odstup</text>
<text x="{(split + 768) / 2}" y="59">{serial['planned']} pokusů</text>
<text x="{(split + 768) / 2}" y="97">{serial['paired_groups']} skupin · hlavní srovnání</text>
</g></svg><figcaption>Rozdělení stejného plánu; fáze se neslévají do výkonového žebříčku.</figcaption></figure>"""


def metrics_table(summary: dict, *, sheet=False) -> str:
    rows = []
    for variant in VARIANTS:
        metric = summary["metrics"][variant]
        rows.append([
            NAMES[variant] if not sheet else {"deepagents": "Deep Agents", "maf": "MAF", "copilot": "Copilot", "pi": "Pi"}[variant],
            f"{metric['passed']}/{metric['attempts']}",
            number(metric["median_success_seconds"]) + " s",
            f"{number(metric['median_input_tokens'], 0)} / {number(metric['median_output_tokens'], 0)}",
            number(metric["mean_success_estimated_usd"], 3),
            number(metric["quality_mean_including_failures"], 2),
        ])
    return table(["Varianta", "Úspěch", "p50 úspěch", "Vstup / výstup", "USD / úspěch", "Kvalita / 4"], rows, sheet=sheet)


def primitive_table(*, sheet=False):
    return table(
        ["Primitivum", "Deep Agents", "MAF", "Copilot SDK", "Pi"],
        [
            ["Nativní exekuce", "LangGraph agent", "Harness agent", "CLI runtime", "Node session"],
            ["App tools", "StructuredTool", "@tool", "Tool + handler", "customTools"],
            ["Plán v tomto profilu", "TODO middleware", "TODO provider", "Konverzace", "Konverzace"],
            ["Usage", "Message metadata", "Response aggregate", "Usage events", "Message events"],
            ["Kde běží host", "Python", "Python", "Python + runtime", "Python + Node bridge"],
        ], sheet=sheet,
    )


def blocks(items: list[tuple[str, str]]) -> str:
    return '<div class="sheet-grid">' + "".join(
        f'<section class="sheet-block"><h3>{escape(title)}</h3><p>{escape(body)}</p></section>'
        for title, body in items
    ) + "</div>"


def page(slug: str, kicker: str, title: str, lead: str, body: str, notes: str) -> str:
    return (
        f'<article class="sheet-page" id="sheet-{slug}"><header class="sheet-head">'
        f'<p class="sheet-kicker">{escape(kicker)}</p><h2 class="sheet-title">{escape(title)}</h2>'
        f'<p class="sheet-lead">{escape(lead)}</p></header>{body}'
        f'<footer class="sheet-notes"><p>{escape(notes)}</p></footer></article>'
    )


async def build_report() -> int:
    summary = aggregate()
    complete = summary["status"] == "complete"
    initial = next((phase for phase in summary["phases"] if phase["id"] == "parallel_quota"), None)
    status = "144 zaznamenaných pokusů; 144 zaslepených hodnocení." if complete else (
        f"NEÚPLNÉ: {summary['attempts']}/144 pokusů, {summary['quality_coverage']}/144 hodnocení. Nejde o finální benchmark."
    )
    if initial:
        status += f" Hlavní srovnání: {summary['primary_planned']} sériových pokusů; původních {initial['planned']} paralelních zvlášť."
    if summary.get("execution_status") == "capped":
        status += " CAPPED: časové okno skončilo po hibernaci počítače."
    elif summary.get("paused_reason"):
        status += " Pozastaveno: " + summary["paused_reason"]
    intro_items = [
        ("Explorace není pevný workflow", "Agent zvolí dotazy, prohlédne nalezené pasáže, hledá protiargument a opraví draft. Dopředu neznáme konkrétní zdroje ani potřebná navazující ověření."),
        ("Publikace není agentská improvizace", "Host přijímá jen strukturovaný brief. Ověří zdroje, doslovné citace, různorodost a délku; bezpečně vykreslí šablonu a přes Chromium vytvoří PDF."),
        ("Stejný model, reálné služby", "Všechny varianty používají gpt-6-1-sol v existujícím Foundry přes Entra a stejný WebIQ MCP. Zadání jsou hypotetická; dotazy, odpovědi a artefakty jsou skutečné."),
        ("Důvěra má omezený význam", "Validní citace není automaticky správný závěr. WebIQ účet vrací indexované pasáže, ne plné stránky. Aplikace nevykonává doporučené nasazení ani obchodní rozhodnutí."),
    ]
    body = '<section class="chapter" id="ch-application"><h2 class="chapter-label">Aplikace</h2>'
    body += card("thesis", "Harness řídí práci. Důvěru vlastní aplikace.",
                 f"<p>{escape(status)}</p><p>Porovnáváme dva směry sbližování: framework rozšířený o harness "
                 "a hotový harness použitý jako součást aplikace. Oba musí projít stejnou "
                 "hranicí schopností, důkazů a publikace.</p>" + diagram(), lead=True)
    body += card("scenario", "Rozhodovací brief potřebuje hledání i opravy", blocks(intro_items))
    body += card("boundary", "Pět úzkých nástrojů stačí na skutečnou rešeršní smyčku",
                 "<p><code>search_web → inspect_source → save_draft → validate_report → publish_report</code>. "
                 "Šipky popisují obvyklý průchod, nikoli ručně napsanou agentskou smyčku. Další dotaz i opravu "
                 "volí příslušný nativní harness. WebIQ klíč zůstává v procesu hosta; model jej nedostává.</p>"
                 "<p>Nejvýše 6 hledání, 5 různých čtených zdrojů, 18 společných tool calls a 360 sekund. "
                 "Modelový limit je 24; Deep jej kontroluje callbackem, MAF limitem iterací a Node runtimes "
                 "po assistant response. Nejde o totožný pre-request mechanismus. Nativní TODO není společný "
                 "aplikační tool call. Shell ani agentem dodané HTML nebo výstupní cesty nejsou přípustné.</p>"
                 "<p>Trvale uložený SQLite ledger hlídá 240 operací, odhad 200 USD a 10 000 WebIQ volání. "
                 "Neúspěch s neúplnou usage ponechá rezervu 3,25 USD. Read-only WebIQ HTTP 429/502/503/504 "
                 "má jediný zaznamenaný retry; případné opakované search volání se znovu účtuje.</p>")
    body += '</section><section class="chapter" id="ch-primitives"><h2 class="chapter-label">Primitives</h2>'
    body += card("matrix", "Rozdíl je v zabalení smyčky, ne v odpovědnosti aplikace", primitive_table()
                 + "<p>Tabulka popisuje skutečně použitý profil, nikoli úplný katalog produktů. MCP připojení "
                 "je záměrně ve společném hostu; neměříme zde kvalitu čtyř různých MCP konektorů. "
                 "Filesystem, shell, delegace a skills nejsou cílem tohoto úzkého scénáře.</p>")
    loc_rows = [[NAMES[v], str(summary["code"]["adapters_nonblank_loc"][v])] for v in VARIANTS]
    body += card("effort", "Krátký factory call nenahrazuje integrační práci",
                 table(["Nativní adaptér", "Neprázdné řádky včetně instrumentace"], loc_rows)
                 + f"<p>Společná aplikace: {summary['code']['common_application_nonblank_loc']} neprázdných "
                 "Python řádků. Pi zahrnuje i Node driver a JSONL bridge; ostatní adaptéry také zahrnují "
                 "identity, limity a observability. Testy, benchmark, report generator a vendored html-docs "
                 "assets nejsou v tomto čísle. LOC charakterizuje tento profil, ne obecnou produktivitu.</p>")
    body += '</section><section class="chapter" id="ch-runtimes"><h2 class="chapter-label">Čtyři cesty</h2>'
    for variant in VARIANTS:
        profile = PROFILES[variant]
        body += card(variant, profile["title"],
                     "".join(f"<h4>{label}</h4><p>{escape(profile[key])}</p>" for label, key in [
                         ("Jak funguje", "wiring"), ("Silná stránka", "strength"), ("Nevýhoda", "cost"), ("Rozsah důkazu", "scope"),
                     ]) + f"<pre><code>{escape(profile['code'])}</code></pre>"
                     f'<p>Schéma API zapojení; plný runnable kód: <code>harness_lab\\adapters.py</code>'
                     f'{" a <code>pi.mjs</code>" if variant == "pi" else ""}. '
                     f'Dokumentace: <a href="#source-{profile["citation"]}">[{profile["citation"]}]</a>.</p>')
    body += '</section><section class="chapter" id="ch-results"><h2 class="chapter-label">Měření</h2>'
    body += card("results", "Úspěch, rychlost a kvalita musí zůstat oddělené",
                 f"<p><strong>{escape(status)}</strong></p>" + metrics_table(summary)
                 + f"<p>Tabulka a statistiky čtyř variant používají pouze hlavní sériovou kohortu: "
                 f"{summary['primary_attempts']}/{summary['primary_planned']} zaznamenaných pokusů. "
                 "Rychlost je medián end-to-end času pouze úspěšných pokusů. Tokeny jsou mediány "
                 "úspěšných pokusů s usage; vstup zahrnuje cache read i cache write. Cena je průměr "
                 "modelového a WebIQ odhadu za úspěch, nikoli cena garance úspěšného dodání. "
                 "Kvalita je průměr pěti kritérií 0–4 přes všechny hodnocené pokusy; neúspěchy mají nulu. "
                 "Plánovaný 60sekundový odstup mezi úlohami není zahrnut v latenci jednotlivého pokusu.</p>")
    if initial:
        body += card("quota-phase", "Paralelní fáze je diagnostika sdílené kvóty",
                     f"<p>Původních {initial['planned']} pokusů v {initial['paired_groups']} celých skupinách: "
                     f"{initial['statuses'].get('passed', 0)} úspěšných, "
                     f"{initial['statuses'].get('failed', 0)} selhaných a "
                     f"{initial['statuses'].get('interrupted', 0)} operátorem přerušených. "
                     "Čtyři workery sdílely Foundry limit 100 000 tokenů a 100 požadavků za minutu. "
                     "Byly potvrzeny HTTP 429 token-rate chyby, ale ne každé selhání lze připsat kvótě; "
                     "objevily se i chyby spuštění Azure CLI. Přerušení při zastavení kampaně nejsou "
                     "nativními chybami SDK. Žádný z těchto pokusů nebyl nahrazen.</p>"
                     + metrics_table(initial)
                     + "<p>Tato tabulka není čistý test intrinsic výkonu harnessů. Výsledky a nulová "
                     "hodnocení neúspěchů zůstávají zachovány, ale neovlivňují hlavní sériovou tabulku.</p>")
    delivery_errors = Counter(
        (record["variant"], record.get("error_type") or record["status"])
        for record in summary["records"]
        if record.get("measurement_phase") == summary["primary_phase"]
        and record["status"] in {"failed", "interrupted"}
    )
    if delivery_errors:
        body += card("delivery-errors", "Přesvědčivá odpověď ještě není dodaný report",
                     table(["Sériová varianta", "Zaznamenaný typ selhání", "Počet"], [
                         [NAMES[variant], error, str(count)]
                         for (variant, error), count in sorted(delivery_errors.items())
                     ])
                     + "<p>PublicationMissing znamená, že harness skončil bez ověřené HTML/PDF publikace. "
                     "Je to výsledek na aplikační hranici, nikoli automatická diagnóza SDK nebo kvóty. "
                     "I užitečně znějící konverzační odpověď bez artefaktů zůstává neúspěchem; host ji "
                     "nevydává za dokončenou práci. Neúspěšné pokusy mají ve zmrazené evaluaci nulu.</p>")
    if summary.get("operational_context"):
        body += card("host-suspension", "Hibernace hosta není pomalý harness",
                     "<p>Windows zaznamenal uspání a pozdější návrat z hibernace i během experimentu. "
                     "Intervaly 33 342 sekund u neúspěšného Deep Agents a 3 216 sekund u úspěšného "
                     "Copilotu nelze interpretovat jako rychlost nativních SDK. Oba původní výsledky, "
                     "usage i rezervy zůstávají beze změny; pokusy nebyly nahrazeny. V původním časovém okně "
                     "scheduler po probuzení další modelovou práci nezahájil; pokračování vyžadovalo "
                     "nové schválení vlastníka.</p>"
                     + table(["Běh", "Uspání UTC", "Návrat UTC"], [
                         [item["run_id"], item["sleep_at"], item["resume_at"]]
                         for item in summary["operational_context"]
                     ])
                     + "<p>Důkaz: Microsoft-Windows-Kernel-Power, události 42 a 507. Jde o provozní "
                     "omezení lokálního experimentu, ne srovnávací vlastnost harnessu. Timeout se "
                     "nemůže vyhodnotit, když host neběží. Časy perf_counter a systémové hodiny se "
                     "po hibernaci rozešly; cooldown proto kontrolujeme mezi časem zápisu původního "
                     "result.json a startem dalšího ledger řádku, obojí v Unix čase. Neodečítáme "
                     "nesouměřitelné hodiny ani neodstraňujeme pomalé výsledky z tabulky.</p>")
    timing_rows = [
        [NAMES[v], number(summary["metrics"][v]["median_setup_seconds"]) + " s",
         number(summary["metrics"][v]["median_export_seconds"]) + " s",
         f'{summary["metrics"][v]["successful_usage_coverage"]}/{summary["metrics"][v]["passed"]}',
         f'{summary["metrics"][v]["quality_coverage"]}/{summary["metrics"][v]["planned"]}']
        for v in VARIANTS
    ]
    body += card("method", "Párované pořadí omezuje, ale neodstraňuje vnější šum",
                 "<p>12 zmrazených briefů × 3 opakování × 4 varianty. Seed 20261022 promíchá 36 skupin "
                 "i pořadí variant uvnitř nich. Po schválené změně protokolu běží zbývající buňky "
                 "sériově se 60sekundovým odstupem; stejně se dávkuje evaluace. Celé skupiny zůstaly "
                 "uvnitř jedné fáze. Model, SDK, per-job limity, společné tools, workload a rubric "
                 "se nezměnily; změna scheduleru má uchovanou historii fingerprintů. Počet opakování "
                 "briefů v hlavní kohortě nemusí být stejný, ale pro všechny čtyři varianty je totožný.</p>"
                 "<p>Sdílejí Azure kvótu, síť, lokální CPU a přirozeně zahřátou provider cache; "
                 "další provoz na deploymentu nelze vyloučit. Nejde o izolovaný cold-cache ani "
                 "univerzální kapacitní test. Zdrojový cutoff je instrukce předaná modelu; host "
                 "neprokazuje historický snapshot živého WebIQ indexu k tomuto datu.</p>"
                 + table(["Varianta", "p50 konfigurace harnessu", "p50 export", "Usage úspěchů", "Hodnocení"], timing_rows)
                 + "<p>Konfigurace harnessu je čas od startu hosta k markeru harness_ready; lazy autentizace "
                 "může následovat až při prvním požadavku. Agentní interval zahrnuje tools i export, "
                 "proto tyto časy nelze prostě sčítat. Import cache procesu a native retry policy "
                 "nejsou mezi SDK dokonale totožné. Zachováváme neúspěchy i jejich časy, žádné tiché náhrady.</p>")
    body += card("quality", "Slepý modelový soud není nezávislá pravda",
                 "<p>Každá skupina dostane náhodné anonymní štítky A–D. Stejný Sol hodnotí rozhodovací "
                 "užitečnost, entailment z dodaných pasáží, protiargumenty, nejistotu a splnění instrukcí. "
                 "Nevidí názvy runtime, náklady ani latenci. Přesné citace a formát kontroluje navíc "
                 "deterministický host. Selhaný pokus dostává pět nul, ne výjimku z průměru.</p>"
                 + table(
                     ["Varianta", "Použitelnost", "Podpora zdrojů", "Protiargument", "Nejistota", "Instrukce"],
                     [[NAMES[v]] + [number(summary["metrics"][v]["quality_criteria_means"][criterion], 2)
                                    for criterion in CRITERIA] for v in VARIANTS],
                 )
                 + "<p>Rozpad kritérií patří pouze sériové kohortě a zahrnuje i nulová hodnocení selhání.</p>"
                 + table(
                     ["Varianta", "Kvalita úspěšných briefů / 4", "Včetně nulových selhání / 4"],
                     [[NAMES[v], number(summary["metrics"][v]["quality_mean_success_only"], 2),
                       number(summary["metrics"][v]["quality_mean_including_failures"], 2)] for v in VARIANTS],
                 )
                 + "<p>Podmíněné skóre úspěchů nenahrazuje hlavní skóre včetně selhání. Odděluje "
                 "kvalitu dodaného obsahu od spolehlivosti celé aplikace; DNS a uspání hosta nejsou "
                 "samy o sobě vlastností kvality nativního harnessu.</p>"
                 "<p>Autor a soudce jsou stejná rodina modelu. Malý technický workload, promptové rozdíly "
                 "nativních harnessů a změny živého web indexu omezují zobecnitelnost. Test neprokazuje "
                 "odolnost vůči všem prompt injections, OS izolaci, restart conversation state ani "
                 "kvalitu kompakce dlouhého kontextu.</p>")
    price = summary["pricing"]
    body += card("cost", "Odhad účtování musí přiznat cache i chybějící usage",
                 f"<p>Global Standard, short context: USD {price['input_per_million']:g} / "
                 f"{price['cached_input_per_million']:g} / {price['cache_write_per_million']:g} / "
                 f"{price['output_per_million']:g} za milion uncached input / cache read / cache write / output tokenů. "
                 "Vzorec nejprve z celkového vstupu oddělí obě cache složky. WebIQ USD 0,007/volání "
                 "je odhad vlastníka, ne ověřený veřejný tarif. Bez Azure faktury nejde o skutečně vyúčtované náklady.</p>"
                 f"<p>Ledger v době sestavení: {summary['operations']} operací včetně pilotů a evaluací, "
                 f"{summary['webiq_calls']} WebIQ volání; konzervativní obálka "
                 f"{number(summary['conservative_spending_envelope_usd'], 2)} USD včetně "
                 f"{summary['runs_with_retained_unknown_usage_reservation']} rezerv pro neúplnou usage. "
                 "Raw evidence zůstává lokálně v ignorovaném runs; bezpečný agregát a hashe jsou v results.</p>")
    body += '</section><section class="chapter" id="ch-evidence"><h2 class="chapter-label">Důkazy a rozhodnutí</h2>'
    body += card("choice", "Vyberte integrační hranici, ne vítěze názvu",
                 "<p>Pro existující Python aplikaci začněte Deep Agents nebo MAF a porovnejte, který "
                 "model middleware/providers lépe odpovídá potřebné kontrole. Pro aplikaci nad hotovým "
                 "runtime zvažte Copilot SDK; pro explicitní malý Node SDK core Pi. V tomto Python "
                 "scénáři se do ceny Pi započítává i mezijazykové propojení.</p>"
                 "<p>Rozhodující není samotné slovo harness. Rozhodující je, kdo vlastní execution, "
                 "credentials, povolené schopnosti, perzistenci a konečné rozhodnutí o publikaci.</p>")
    versions = {**summary["python_packages"], **summary.get("runtime_versions", {})}
    body += card("sources", "Tvrzení mají dohledatelný rozsah důkazů",
                 "<p>Stav dokumentace a zdrojový cutoff: 22. 10. 2026. Verze balíčků jsou zmrazené "
                 "lockfiles a fingerprintem experimentu. Sol deployment byl ověřen přes existující "
                 "Foundry účet: gpt-6.1-sol, verze 2026-09-29; lokální autentizace používá Entra.</p>"
                 + table(["Komponenta", "Zmrazená verze"], [[key, str(value)] for key, value in versions.items()])
                 + "<ol>"
                 + "".join(
                     f'<li id="source-{key}"><a href="{escape(url)}">[{key}] {escape(title)}</a>. {escape(note)}</li>'
                     for key, title, url, note in SOURCES
                 ) + "</ol><p>WebIQ pricing page nedostupná; pokus o odhadnutou MAF source URL skončil 404 "
                 "a nebyl použit. Nepročtené široké search výsledky nejsou důkaz. Community sentiment, "
                 "interní M365 obsah a cloud deployment jsou mimo rozsah. Úplný kompaktní manifest: "
                 "<code>results\\sources.json</code>.</p>")
    body += "</section>"
    sheet = '<section class="sheet" aria-label="Šestistránkový comparison paper">'
    sheet += page("intro", "1 / 6 · Praktický experiment", "Dvě cesty k agentní aplikaci",
                  "Deep Agents, Agent Framework, Copilot SDK a Pi nad stejným Foundry Sol a WebIQ.",
                  diagram() + blocks(intro_items)
                  + f'<aside class="sheet-actions"><h3>Stav experimentu</h3><p>{escape(status)}</p></aside>'
                  '<p class="sheet-close">Agent zkoumá. Aplikace drží hranice.</p>',
                  "22. 10. 2026. Hypotetické briefy, reálné API a artefakty. [D, M, C, P, W, S] Odkazy a metodika jsou v Read view stejného HTML.")
    sheet += page("compare", "2 / 6 · Primitives a výsledky", "Smyčka není hranice důvěry",
                  "Stejných pět aplikačních nástrojů; různé nativní instrukce, plánování a integrační náklady.",
                  primitive_table(sheet=True)
                  + f'<h3 class="sheet-section-title">Sériová kohorta: {summary["primary_planned"]} pokusů</h3>'
                  + metrics_table(summary, sheet=True)
                  + "<p>p50: sekundy pouze úspěšných dodání. Vstup/výstup: medián tokenů úspěchů; "
                  "vstup zahrnuje cache. USD: průměr úspěchů včetně WebIQ. Kvalita: 0–4 přes všechny "
                  "hodnocené pokusy; neúspěch = 0. N/A znamená chybějící měření, nikoli nulu.</p>"
                  + phase_diagram(summary)
                  + '<p class="sheet-close">Host je společný. Native overhead není skryt.</p>',
                  f"{status} Celkem 12 briefů × 3 opakování. Latence neobsahuje odstupy mezi úlohami; "
                  "síť, cache a sdílený deployment omezují zobecnitelnost.")
    for index, variant in enumerate(VARIANTS, 3):
        profile, metric = PROFILES[variant], summary["metrics"][variant]
        stats = '<ul class="sheet-stats">' + "".join(
            f'<li><span class="sheet-stat-value">{escape(value)}</span><span class="sheet-stat-label">{escape(label)}</span></li>'
            for value, label in [
                (f"{metric['passed']}/{metric['attempts']}", "Sériová úspěšná dodání"),
                (number(metric["median_success_seconds"]) + " s", "p50 úspěšného běhu"),
                (number(metric["quality_mean_including_failures"], 2), "Kvalita / 4 včetně selhání"),
                (str(summary["code"]["adapters_nonblank_loc"][variant]), "Adaptér LOC včetně instrumentace"),
            ]
        ) + "</ul>"
        sheet += page(
            variant, f"{index} / 6 · {NAMES[variant]}", profile["title"], profile["primitive"],
            stats + blocks([
                ("Jak funguje", profile["wiring"]), ("V čem je silný", profile["strength"]),
                ("Co stojí integrace", profile["cost"]), ("Co test opravdu pokrývá", profile["scope"]),
            ]) + f'<pre><code>{escape(profile["code"])}</code></pre>'
            + '<p class="sheet-close">Důvěryhodnou publikaci zajišťuje host.</p>',
            f"[{profile['citation']}] Stav 22. 10. 2026. API schéma, nikoli samostatný runnable snippet. "
            f"Plný kód: harness_lab\\adapters.py{' + pi.mjs' if variant == 'pi' else ''}. "
            "Modelový soud není lidská validace; neověřujeme dlouhý kontext ani více agentů.",
        )
    sheet += "</section>"
    source = ROOT / "report" / "comparison.html"
    source.write_text(document(
        "Harness řídí práci. Důvěru vlastní aplikace.",
        "Praktické srovnání Deep Agents, Microsoft Agent Framework, GitHub Copilot SDK a Pi.",
        body, "agent-harness-comparison-20261022", sheet=sheet,
    ), encoding="utf-8")
    standalone = await bundle(source)
    pdf = ROOT / "report" / "comparison.paper.pdf"
    pages = await print_pdf(standalone, pdf, sheet=True)
    print(json.dumps({"status": summary["status"], "source": str(source), "standalone": str(standalone), "paper": str(pdf), "pages": pages}))
    return 0 if complete else 1
