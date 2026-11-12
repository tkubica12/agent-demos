from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .core import ROOT, escape

if TYPE_CHECKING:
    from .core import ResearchRun


def card(slug: str, title: str, body: str, *, lead: bool = False) -> str:
    classes = "card card--lead" if lead else "card"
    return (
        f'<article class="{classes}" id="card-{slug}" data-open>'
        '<h3 class="card-head"><button type="button" class="card-toggle" aria-expanded="true">'
        '<span class="card-num" aria-hidden="true"></span><span class="card-titles">'
        f'<span class="card-title">{escape(title)}</span></span>'
        '<span class="card-mark" aria-hidden="true"></span></button></h3>'
        f'<div class="card-body">{body}</div></article>'
    )


def document(title: str, subtitle: str, body: str, doc_id: str, *, sheet: str = "") -> str:
    template = (ROOT / "report" / "research.template.html").read_text(encoding="utf-8")
    head = template.split("<body>", 1)[0]
    head = head.replace("ARTICLE TITLE", escape(title)).replace("ONE SENTENCE SUMMARY", escape(subtitle))
    head = head.replace('content="SLUG"', f'content="{escape(doc_id)}"')
    license_text = (ROOT / "report" / "LICENSE").read_text(encoding="utf-8")
    sheet_control = (
        '<button type="button" class="ctrl" data-action="toggle-sheet" aria-pressed="false">Sheet</button>'
        if sheet else ""
    )
    return (
        f"{head}<body><!--\n{license_text}\n-->"
        '<div class="controls" role="group" aria-label="Document controls">'
        '<button type="button" class="ctrl" data-action="expand-all">Expand all</button>'
        '<button type="button" class="ctrl" data-action="collapse-all">Collapse all</button>'
        f'{sheet_control}<button type="button" class="ctrl" data-action="print">PDF</button>'
        '<button type="button" class="ctrl" data-action="toggle-theme">Dark</button>'
        '<button type="button" class="ctrl" data-action="toggle-accent">Accent: Blue</button></div>'
        f'<div class="doc"><header class="doc-header" id="opening">'
        f'<p class="eyebrow">RESEARCH / EVIDENCE / DECISION</p><h1>{escape(title)}</h1>'
        f'<p class="subtitle">{escape(subtitle)}</p></header><main>{body}</main>'
        '<section class="takeaway" id="closing"><p>Rozhodnuti patri aplikaci; '
        'agent pripravuje overitelne podklady.</p></section></div>'
        f"{sheet}</body></html>"
    )


async def bundle(source: Path) -> Path:
    process = await asyncio.create_subprocess_exec(
        "node", str(ROOT / "report" / "assets" / "bundle.js"), str(source),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"HTML export failed: {stderr.decode()} {stdout.decode()}")
    return source.with_name(source.stem + ".standalone.html")


async def print_pdf(source: Path, destination: Path, *, sheet: bool = False) -> int:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.route("http://**/*", lambda route: route.abort())
            await context.route("https://**/*", lambda route: route.abort())
            page = await context.new_page()
            await page.goto(source.as_uri(), wait_until="load")
            await page.evaluate(
                "(view) => {document.documentElement.dataset.view=view;"
                "document.documentElement.dataset.print=view;}",
                "sheet" if sheet else "read",
            )
            await page.emulate_media(media="print")
            if sheet:
                overflowing = await page.locator(".sheet-page").evaluate_all(
                    "pages => pages.filter(p => p.scrollHeight > p.clientHeight + 2).map(p=>p.id)"
                )
                if overflowing:
                    raise RuntimeError(f"Paper pages overflow: {overflowing}")
            await page.pdf(
                path=str(destination), prefer_css_page_size=True,
                print_background=True, tagged=True, outline=True,
            )
            import re
            pages = len(re.findall(rb"/Type\s*/Page\b", destination.read_bytes()))
            if sheet and pages != await page.locator(".sheet-page").count():
                raise RuntimeError(f"Unexpected PDF page count: {pages}")
            return pages
        finally:
            await browser.close()


async def publish(run: ResearchRun) -> list[str]:
    if run.draft is None:
        raise ValueError("No draft.")
    report = run.draft
    body = card("decision", report.title, f"<p>{escape(report.recommendation)}</p>", lead=True)
    for index, finding in enumerate(report.findings):
        quotes = []
        for evidence in finding.evidence:
            source = run.sources[evidence.source_id]
            quotes.append(
                f'<blockquote><p>{escape(evidence.quote)}</p></blockquote>'
                f'<p><a href="{escape(source["url"])}">{escape(source["title"])}</a>'
                " — WebIQ indexed passage; not a full-page read.</p>"
            )
        body += card(f"finding-{index}", finding.claim, "".join(quotes))
    body += card("counterargument", "Protiargument muze zmenit rozhodnuti", f"<p>{escape(report.counterargument)}</p>")
    body += card(
        "uncertainties", "Nejistoty potrebuji dalsi overeni",
        "<ul>" + "".join(f"<li>{escape(text)}</li>" for text in report.uncertainties) + "</ul>"
        "<h4>Dalsi kroky</h4><ol>" + "".join(f"<li>{escape(text)}</li>" for text in report.next_steps) + "</ol>",
    )
    body = (
        '<section class="chapter" id="ch-decision"><h2 class="chapter-label">Rozhodovaci podklad</h2>'
        + body + "</section>"
    )
    output = document(report.title, run.brief["question"], body, run.id)
    relative_assets = os.path.relpath(ROOT / "report" / "assets", run.directory).replace("\\", "/")
    output = output.replace('"assets/', f'"{relative_assets}/')
    source = run.directory / "report.html"
    source.write_text(output, encoding="utf-8")
    started = __import__("time").perf_counter()
    standalone = await bundle(source)
    pdf = run.directory / "report.pdf"
    pages = await print_pdf(standalone, pdf)
    run.log("publication", pdf_pages=pages, export_seconds=__import__("time").perf_counter() - started)
    return [str(standalone), str(pdf)]
