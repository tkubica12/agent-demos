"""Rendered behaviour checks for the attendee HTML system."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "docs" / "assets"
TEMPLATE = REPO_ROOT / "templates" / "lab-guide-template.html"

#: The published pages, rendered from their real location so relative assets resolve.
PUBLISHED = sorted((REPO_ROOT / "docs").rglob("*.html"))


@pytest.fixture(scope="module")
def rendered_page(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Place the guide template where its relative asset paths resolve."""
    root = tmp_path_factory.mktemp("guide")
    shutil.copytree(ASSETS, root / "assets")
    guides = root / "guides"
    guides.mkdir()
    page = guides / "guide.html"
    page.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return page.as_uri()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as driver:
        browser = driver.chromium.launch()
        yield browser
        browser.close()


def open_page(browser, url: str, *, width: int = 1280, height: int = 720, dark: bool = False):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme="dark" if dark else "light",
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_load_state("load")
    page.wait_for_function("document.documentElement.dataset.enhanced === 'true'")
    return context, page, errors


def test_page_renders_without_console_errors(browser, rendered_page: str) -> None:
    context, page, errors = open_page(browser, rendered_page)
    try:
        assert errors == [], f"console reported errors: {errors}"
        assert page.locator("h1").first.is_visible()
    finally:
        context.close()


def test_stylesheet_is_applied(browser, rendered_page: str) -> None:
    context, page, _ = open_page(browser, rendered_page)
    try:
        family = page.evaluate("getComputedStyle(document.body).fontFamily")
        assert "Segoe UI" in family, f"shared stylesheet did not apply: {family}"
    finally:
        context.close()


def test_system_preference_selects_dark_theme(browser, rendered_page: str) -> None:
    light_context, light_page, _ = open_page(browser, rendered_page, dark=False)
    dark_context, dark_page, _ = open_page(browser, rendered_page, dark=True)
    try:
        assert light_page.evaluate("document.documentElement.dataset.theme") == "light"
        assert dark_page.evaluate("document.documentElement.dataset.theme") == "dark"
        light_bg = light_page.evaluate("getComputedStyle(document.body).backgroundColor")
        dark_bg = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert light_bg != dark_bg
    finally:
        light_context.close()
        dark_context.close()


def test_theme_toggle_switches_and_persists(browser, rendered_page: str) -> None:
    context, page, _ = open_page(browser, rendered_page)
    try:
        toggle = page.locator("[data-theme-toggle]")
        assert toggle.is_visible(), "theme toggle stayed hidden after enhancement"
        toggle.click()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"

        page.reload()
        page.wait_for_function("document.documentElement.dataset.enhanced === 'true'")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    finally:
        context.close()


def test_commands_are_copyable(browser, rendered_page: str) -> None:
    context, page, _ = open_page(browser, rendered_page)
    try:
        copy = page.locator(".code [data-copy]").first
        assert copy.is_visible(), "no copy control was added to the command block"
    finally:
        context.close()


def test_skip_link_is_the_first_focus_target(browser, rendered_page: str) -> None:
    context, page, _ = open_page(browser, rendered_page)
    try:
        page.keyboard.press("Tab")
        focused = page.evaluate("document.activeElement.className")
        assert "skip-link" in focused, f"first focus target was {focused!r}"
    finally:
        context.close()


def test_disclosures_are_keyboard_operable(browser, rendered_page: str) -> None:
    context, page, _ = open_page(browser, rendered_page)
    try:
        summary = page.locator("details > summary").first
        summary.focus()
        page.keyboard.press("Enter")
        assert page.locator("details").first.evaluate("node => node.open")
    finally:
        context.close()


@pytest.mark.parametrize(
    ("width", "height"),
    [(1920, 1080), (1280, 720), (390, 844)],
    ids=["projected", "laptop", "phone"],
)
def test_layout_does_not_overflow_horizontally(
    browser, rendered_page: str, width: int, height: int
) -> None:
    context, page, _ = open_page(browser, rendered_page, width=width, height=height)
    try:
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"page overflows horizontally by {overflow}px at {width}x{height}"
    finally:
        context.close()


@pytest.mark.parametrize("document", PUBLISHED, ids=lambda path: path.name)
def test_published_page_renders_without_console_errors(browser, document: Path) -> None:
    context, page, errors = open_page(browser, document.as_uri())
    try:
        assert errors == [], f"{document.name} reported console errors: {errors}"
        assert page.locator("h1").first.is_visible()
    finally:
        context.close()


@pytest.mark.parametrize("document", PUBLISHED, ids=lambda path: path.name)
@pytest.mark.parametrize(
    ("width", "height"),
    [(1920, 1080), (1280, 720), (390, 844)],
    ids=["projected", "laptop", "phone"],
)
def test_published_page_does_not_overflow_horizontally(
    browser, document: Path, width: int, height: int
) -> None:
    """Screenshots are the usual cause, so every published page is checked, not the template."""
    context, page, _ = open_page(browser, document.as_uri(), width=width, height=height)
    try:
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, (
            f"{document.name} overflows horizontally by {overflow}px at {width}x{height}"
        )
    finally:
        context.close()


@pytest.mark.parametrize("document", PUBLISHED, ids=lambda path: path.name)
def test_published_images_load(browser, document: Path) -> None:
    """A broken screenshot path still renders as an alt-text box, so check the pixels."""
    context, page, _ = open_page(browser, document.as_uri())
    try:
        broken = page.evaluate(
            "Array.from(document.images)"
            ".filter(image => !image.complete || image.naturalWidth === 0)"
            ".map(image => image.getAttribute('src'))"
        )
        assert not broken, f"{document.name} has images that did not load: {broken}"
    finally:
        context.close()
