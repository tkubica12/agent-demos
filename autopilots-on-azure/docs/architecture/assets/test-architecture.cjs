#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { playwright, browserPath } = require("./browser-support.cjs");

const args = process.argv.slice(2);
if (!args[0] || args[0].startsWith("--") ||
  (args.length !== 1 && !(args.length === 3 && args[1] === "--shots" && args[2]))) {
  console.error("Usage: node assets\\test-architecture.cjs path-to.html [--shots DIR]");
  process.exit(2);
}
const file = path.resolve(args[0]);
assert.ok(fs.existsSync(file), `HTML document exists: ${file}`);
const shots = args[1] === "--shots" ? path.resolve(args[2]) : null;
if (shots) fs.mkdirSync(shots, { recursive: true });
const url = pathToFileURL(file).href;
const diagramIds = ["diagram-infrastructure", "diagram-application", "diagram-message", "diagram-document"];
let checks = 0;

function check(name, value) {
  assert.ok(value, name);
  checks++;
}

async function frame(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function visiblePopup(page, pinned) {
  const popup = page.locator(".diagram-popup");
  await popup.waitFor({ state: "visible" });
  check(pinned ? "pinned popup is a nonmodal dialog" : "hover popup is a named region",
    await popup.getAttribute("role") === (pinned ? "dialog" : "region"));
  check("popup is named", !!await popup.getAttribute("aria-labelledby"));
  check("only pinned popup has a visible close button", await popup.locator(".diagram-popup-close").isVisible() === pinned);
  if (pinned) check("popup does not claim modality", await popup.getAttribute("aria-modal") === "false");
  await frame(page);
  await popupBounds(page);
  return popup;
}

async function popupBounds(page) {
  check("popup fits the viewport", await page.locator(".diagram-popup").evaluate(node => {
    const box = node.getBoundingClientRect();
    return box.left >= -1 && box.top >= -1 &&
      box.right <= window.innerWidth + 1 && box.bottom <= window.innerHeight + 1;
  }));
}

async function uniqueIds(page) {
  check("no duplicate IDs, including popup clones", await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[id]")].map(node => node.id);
    return ids.length === new Set(ids).size;
  }));
}

async function expandCards(page) {
  const expand = page.locator('[data-action="expand-all"]');
  if (await expand.count()) await expand.first().click();
  await frame(page);
}

async function structure(page) {
  check("document is reading-only", await page.locator('[data-action="toggle-slides"]').count() === 0);
  check("runtime initialized", await page.locator("html").getAttribute("data-architecture-ready") !== null);
  check("four expected diagrams exist", await page.locator("[data-diagram]").count() === diagramIds.length);
  for (const id of diagramIds) {
    const figure = page.locator(`#${id}`);
    check(`${id}: SVG and focus control`, await figure.locator("svg.architecture-svg").count() === 1 &&
      await figure.locator(".diagram-focus").count() === 1);
    check(`${id}: named SVG group`, await figure.locator("svg").getAttribute("role") === "group" &&
      !!await figure.locator("svg").getAttribute("aria-labelledby"));
    check(`${id}: interactive nodes`, await figure.locator(".arch-node").count() > 0);
  }
  const errors = await page.evaluate(() => [...document.querySelectorAll(".arch-node")].flatMap(node => {
    const id = node.dataset.detail;
    const detail = document.getElementById(id);
    const summary = detail?.querySelector(":scope > summary");
    const copy = detail?.querySelector(":scope > .component-copy");
    const href = node.getAttribute("href");
    return detail?.matches(".component-detail") && summary?.textContent.trim() && copy?.textContent.trim() &&
      href?.startsWith("#") && decodeURIComponent(href.slice(1)) === id && node.getAttribute("aria-label")
      ? [] : [`${id || "(missing data-detail)"}: invalid accessible detail reference`];
  }));
  check(`all nodes resolve static detail content: ${errors.join("; ")}`, errors.length === 0);
  check("every relationship endpoint resolves", await page.evaluate(() =>
    [...document.querySelectorAll(".edge[data-from], .edge[data-to]")].every(edge =>
      [edge.dataset.from, edge.dataset.to].filter(Boolean).every(id => document.getElementById(id)?.matches(".component-detail")))));
  check("SVG node labels do not inherit link underlines", await page.evaluate(() =>
    [...document.querySelectorAll(".arch-node, .arch-node text")].every(element =>
      getComputedStyle(element).textDecorationLine === "none")));
  check("popup content is not a visible reference chapter", await page.locator("#ch-reference").count() === 0 &&
    !await page.locator("[data-popup-reference]").isVisible());
  check("application explanations stay concise", await page.evaluate(() => {
    const ids = new Set([...document.querySelectorAll("#diagram-application .arch-node")].map(node => node.dataset.detail));
    return [...ids].every(id => document.querySelector(`#${id} .component-copy`).textContent.trim().split(/\s+/).length <= 50);
  }));
  await uniqueIds(page);
}

async function highlightGeometry(page) {
  const figure = page.locator("#diagram-infrastructure");
  const geometry = () => figure.evaluate(element =>
    [...element.querySelectorAll(".edge, .arch-node > rect, marker")].map(item => {
      const box = item.getBBox?.();
      return {
        d: item.getAttribute("d"),
        box: box ? [box.x, box.y, box.width, box.height] : null,
        stroke: getComputedStyle(item).strokeWidth,
        markerWidth: item.getAttribute("markerWidth"),
        markerHeight: item.getAttribute("markerHeight"),
        transform: getComputedStyle(item).transform
      };
    }));
  const baseline = await geometry();
  for (const node of await figure.locator(".arch-node").all()) {
    await node.scrollIntoViewIfNeeded();
    await frame(page);
    await figure.locator(".diagram-scroll").focus();
    await frame(page);
    await node.focus();
    await visiblePopup(page, false);
    assert.deepEqual(await geometry(), baseline, "highlight preserves all path, marker and box geometry");
    checks++;
    await page.keyboard.press("Enter");
    await visiblePopup(page, true);
    assert.deepEqual(await geometry(), baseline, "pinning does not resize arrows or boxes");
    checks++;
    await page.keyboard.press("Escape");
  }
  await figure.locator(".diagram-scroll").focus();
}

async function reviewedDesign(page) {
  for (const [id, title] of Object.entries({
    "card-network": "Infrastructure diagram",
    "card-components": "Application architecture",
    "card-message": "Teams message lifecycle",
    "card-document": "Document editing"
  })) {
    check(`${id}: simple descriptive title`, await page.locator(`#${id} .card-title`).textContent() === title);
  }
  check("infrastructure contains only the diagram card", await page.locator("#ch-infrastructure > .card").count() === 1 &&
    await page.locator("#card-hosting").count() === 0);
  check("diagrams use generic runtime terminology", await page.locator(".architecture-svg").evaluateAll(elements =>
    elements.every(element => !/\bHermes\b/.test(element.textContent) &&
      [...element.querySelectorAll("[aria-label]")].every(node => !/\bHermes\b/.test(node.getAttribute("aria-label"))))));
  check("runtime labels and popup agree", await page.locator('[data-detail="detail-runtime"] .node-title')
    .evaluateAll(elements => elements.every(element => element.textContent === "Agent runtime")) &&
    await page.locator("#detail-runtime > summary").textContent() === "Agent runtime");
  check("custom MCP labels and popup agree", await page.locator('[data-detail="detail-private-mcp"] .node-title')
    .evaluateAll(elements => elements.every(element => element.textContent === "Custom MCP")) &&
    await page.locator("#detail-private-mcp > summary").textContent() === "Custom MCP");
  check("tool nodes have names without category numbers", await page.locator("#diagram-infrastructure .node-title")
    .evaluateAll(elements => elements.every(element => !/^\s*\d+\s*\//.test(element.textContent))));
  const infra = page.locator("#diagram-infrastructure");
  for (const [from, to] of [["private-mcp", "vnet"], ["vnet", "hub"], ["hub", "corporate"]]) {
    check(`backend route includes ${from} to ${to}`,
      await infra.locator(`.edge[data-from="detail-${from}"][data-to="detail-${to}"]`).count() === 1);
  }
  check("custom MCP does not bypass the enterprise hub",
    await infra.locator('.edge[data-from="detail-private-mcp"][data-to="detail-corporate"]').count() === 0);
  check("Foundry private endpoint is part of the selected design",
    await infra.locator('[data-detail="detail-foundry-pe"]').evaluate(element => !element.classList.contains("optional-node")) &&
    await infra.locator('.edge[data-to="detail-foundry-pe"], .edge[data-from="detail-foundry-pe"]')
      .evaluateAll(elements => elements.length === 2 && elements.every(element => !element.classList.contains("optional-edge"))));
  check("Foundry text no longer presents a public or optional choice",
    await page.locator("#detail-foundry, #detail-foundry-pe").evaluateAll(elements =>
      elements.every(element => !/\boptional\b|\bpublic\b/i.test(element.textContent))));
}

async function interactions(page) {
  const figure = page.locator("#diagram-infrastructure");
  const node = figure.locator(".arch-node").first();
  const popup = page.locator(".diagram-popup");
  const detailId = await node.getAttribute("data-detail");
  await node.scrollIntoViewIfNeeded();
  await frame(page);
  const priorFocus = await page.evaluateHandle(() => document.activeElement);
  await node.hover();
  await visiblePopup(page, false);
  check("hover never steals focus", await page.evaluate(previous => previous === document.activeElement, priorFocus));
  await priorFocus.dispose();
  check("popup contains the authored explanation", await popup.locator(".component-copy").textContent() ===
    await page.locator(`#${detailId} > .component-copy`).textContent());
  check("active node state is reflected", await node.getAttribute("aria-expanded") === "true");
  check("touching relationships are highlighted without dimming other labels", await node.evaluate(element => {
    const figure = element.closest("[data-diagram]");
    return [...figure.querySelectorAll(".edge[data-from], .edge[data-to]")].every(edge =>
      edge.classList.contains("is-active") ===
        (edge.dataset.from === element.dataset.detail || edge.dataset.to === element.dataset.detail)) &&
      [...figure.querySelectorAll("text")].every(text => Number(getComputedStyle(text).opacity) >= 0.9);
  }));
  await popup.locator(".diagram-popup-title").hover();
  await page.waitForTimeout(300);
  check("pointer can enter and read hover popup", await popup.isVisible());
  await figure.locator(".diagram-label").hover();
  await page.waitForTimeout(300);
  check("hover leaves with bounded dismissal", !await popup.isVisible());
  await node.hover();
  await visiblePopup(page, false);
  await page.evaluate(() => window.scrollBy(0, 40));
  await frame(page);
  check("scroll dismisses an unpinned preview", !await popup.isVisible());

  // Auto-scrolling must not move another node under a parked pointer during keyboard checks.
  await page.mouse.move(0, 0);
  await node.scrollIntoViewIfNeeded();
  await frame(page);
  await node.focus();
  await visiblePopup(page, false);
  check("keyboard focus remains on the node", await node.evaluate(element => document.activeElement === element));
  await page.evaluate(() => window.scrollBy(0, 40));
  await frame(page);
  check("scroll repositions a keyboard-focused preview instead of dismissing it", await popup.isVisible());
  await popupBounds(page);
  await page.keyboard.press("Escape");
  check("Escape dismisses keyboard preview without moving focus", !await popup.isVisible() &&
    await node.evaluate(element => document.activeElement === element));
  await figure.locator(".diagram-scroll").focus();
  await frame(page);
  await node.focus();
  await visiblePopup(page, false);
  await page.keyboard.press("Enter");
  await visiblePopup(page, true);
  if (shots) await page.screenshot({ path: path.join(shots, "1440x900-light-blue-pinned.png") });
  check("keyboard activation focuses the dialog close control", await popup.locator(".diagram-popup-close")
    .evaluate(element => document.activeElement === element));
  await page.keyboard.press("Escape");
  check("Escape dismisses pinned popup", !await popup.isVisible());
  check("Escape restores the node without reopening preview", await node.evaluate(element => document.activeElement === element));
  await page.keyboard.press("Space");
  await visiblePopup(page, true);
  await popup.locator(".diagram-popup-close").click();
  check("close restores node focus", !await popup.isVisible() && await node.evaluate(element => document.activeElement === element));

  await node.click();
  await visiblePopup(page, true);
  await uniqueIds(page);
  await page.evaluate(() => window.scrollBy(0, 60));
  await frame(page);
  check("pinned popup survives page scroll", await popup.isVisible());
  await popupBounds(page);
  await figure.locator(".diagram-label").click();
  check("outside click dismisses popup", !await popup.isVisible());

  await node.click();
  await visiblePopup(page, true);
  check("popup has no link to a removed reference chapter", await popup.locator(".diagram-popup-reference").count() === 0);
  await page.keyboard.press("Escape");
}

async function focusMode(page) {
  const figure = page.locator("#diagram-application");
  const button = figure.locator(".diagram-focus");
  const originalParent = await figure.evaluate(element => element.parentElement.id || element.parentElement.className);
  await button.click();
  check("focus mode uses the original figure", await figure.evaluate(element =>
    element.classList.contains("is-focused") && element.parentElement === document.body));
  check("focus control advertises exit", await button.textContent() === "Exit focus" &&
    await button.getAttribute("aria-pressed") === "true");
  check("focus figure fills the viewport without overflow", await figure.evaluate(element => {
    const box = element.getBoundingClientRect();
    return box.left >= 0 && box.top >= 0 && box.right <= innerWidth &&
      box.bottom <= innerHeight && box.width >= innerWidth - 30;
  }));
  await uniqueIds(page);
  const node = figure.locator(".arch-node").first();
  await node.click();
  const popup = await visiblePopup(page, true);
  if (shots) await page.screenshot({ path: path.join(shots, "1440x900-light-blue-focus.png") });
  check("popup layers above focused diagram", await popup.evaluate(element =>
    Number(getComputedStyle(element).zIndex) > Number(getComputedStyle(document.querySelector(".is-focused")).zIndex)));
  await page.keyboard.press("Escape");
  check("first Escape dismisses popup but retains focus mode", await figure.evaluate(element => element.classList.contains("is-focused")));
  await page.keyboard.press("Escape");
  check("second Escape exits focus mode", !await figure.evaluate(element => element.classList.contains("is-focused")));
  check("focus figure returns to its authored location", await figure.evaluate(element =>
    element.parentElement.id || element.parentElement.className) === originalParent);
  check("focus returns to the invoking control", await button.evaluate(element => document.activeElement === element));
  await button.click();
  await button.click();
  check("focus mode also closes from its own control", await button.getAttribute("aria-pressed") === "false");
  await button.click();
  const outside = page.locator('[data-action="toggle-theme"]').first();
  if (await outside.count()) {
    await outside.focus();
    check("nonmodal focus mode does not trap focus behind its overlay", !await figure.evaluate(element => element.classList.contains("is-focused")));
    check("outgoing focus is retained", await outside.evaluate(element => document.activeElement === element));
  } else {
    await page.keyboard.press("Escape");
  }
}

async function hashAndPrint(page) {
  const id = await page.locator(".arch-node").first().getAttribute("data-detail");
  await page.goto(`${url}?theme=light&accent=blue#${encodeURIComponent(id)}`, { waitUntil: "load" });
  await frame(page);
  await visiblePopup(page, true);
  check("native initial hash opens its diagram and popup", await page.locator(`.arch-node[data-detail="${id}"]`).first()
    .evaluate(element => element.getAttribute("aria-expanded") === "true" && element.closest(".card").hasAttribute("data-open")));
  check("native hash uses the authored popup content", await page.locator(".diagram-popup .component-copy").textContent() ===
    await page.locator(`#${id} > .component-copy`).textContent());
  const otherId = await page.locator(".arch-node").last().getAttribute("data-detail");
  await page.evaluate(target => { location.hash = target; }, otherId);
  await frame(page);
  await visiblePopup(page, true);
  check("hash changes select another component popup", await page.locator(".diagram-popup .component-copy").textContent() ===
    await page.locator(`#${otherId} > .component-copy`).textContent());
  const states = await page.locator(".component-detail").evaluateAll(elements => elements.map(element => element.open));
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.evaluate(() => window.dispatchEvent(new Event("beforeprint")));
  await page.evaluate(() => window.dispatchEvent(new Event("beforeprint")));
  check("printing switches dark reading mode to light without changing saved preferences",
    await page.locator("html").getAttribute("data-theme") === "light");
  check("print expands component references", await page.locator(".component-detail").evaluateAll(elements => elements.every(element => element.open)));
  await page.emulateMedia({ media: "print" });
  check("print omits interaction controls", !await page.locator(".diagram-focus").first().isVisible());
  check("print does not resurrect the removed chapter", !await page.locator("[data-popup-reference]").isVisible());
  check("print removes card shadows", await page.locator(".card").evaluateAll(elements =>
    elements.every(element => getComputedStyle(element).boxShadow === "none")));
  check("print keeps connector contrast", await page.locator(".architecture-svg .edge").evaluateAll(elements =>
    elements.every(element => getComputedStyle(element).opacity === "1")));
  await page.emulateMedia({ media: "screen" });
  await page.evaluate(() => window.dispatchEvent(new Event("afterprint")));
  check("printing restores the reader's theme even after duplicate beforeprint events",
    await page.locator("html").getAttribute("data-theme") === "dark");
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  assert.deepEqual(await page.locator(".component-detail").evaluateAll(elements => elements.map(element => element.open)), states,
    "print restores prior reference states");
  checks++;
}

async function screenshots(page, prefix) {
  if (!shots) return;
  await page.evaluate(() => window.scrollTo(0, 0));
  await frame(page);
  await page.screenshot({ path: path.join(shots, `${prefix}-header.png`) });
  for (const id of diagramIds) {
    const figure = page.locator(`#${id}`);
    await figure.evaluate(element => element.scrollIntoView({ block: "center" }));
    await frame(page);
    await figure.screenshot({
      path: path.join(shots, `${prefix}-${id}.png`),
      style: ".controls { visibility: hidden !important; }"
    });
  }
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await frame(page);
  await page.screenshot({ path: path.join(shots, `${prefix}-end.png`) });
}

async function openPage(browser, options, run) {
  const context = await browser.newContext({ offline: true, reducedMotion: "reduce", ...options });
  const problems = [];
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(4000);
    page.setDefaultNavigationTimeout(15000);
    page.on("console", message => { if (message.type() === "error") problems.push(message.text()); });
    page.on("pageerror", error => problems.push(error.message));
    page.on("requestfailed", request => problems.push(`Failed request: ${request.url()}`));
    page.on("request", request => {
      if (/^https?:/i.test(request.url())) problems.push(`External runtime request: ${request.url()}`);
    });
    await run(page);
    check(`offline runtime is quiet: ${problems.join("; ")}`, problems.length === 0);
  } finally {
    await context.close();
  }
}

(async () => {
  const browser = await playwright().chromium.launch({ executablePath: browserPath(), headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
      for (const theme of ["light", "dark"]) for (const accent of ["blue", "orange", "green"]) {
        await openPage(browser, { viewport, colorScheme: theme === "light" ? "dark" : "light" }, async page => {
          await page.goto(`${url}?theme=${theme}&accent=${accent}`, { waitUntil: "load" });
          await expandCards(page);
          check("requested theme is active", await page.locator("html").getAttribute("data-theme") === theme);
          check("requested accent is active", await page.locator("html").getAttribute("data-accent") === accent);
          check("desktop has no body overflow", await page.evaluate(() =>
            document.documentElement.scrollWidth <= innerWidth + 1));
          await structure(page);
          const node = page.locator(".arch-node").first();
          await node.scrollIntoViewIfNeeded();
          await frame(page);
          await node.hover();
          await visiblePopup(page, false);
          check("node highlight follows canonical accent", await node.locator("rect").first().evaluate(element => {
            const probe = document.createElement("span");
            probe.style.color = "var(--accent)";
            document.body.append(probe);
            const expected = getComputedStyle(probe).color;
            probe.remove();
            return getComputedStyle(element).stroke === expected;
          }));
          await page.keyboard.press("Escape");
          await page.mouse.move(0, 0);
          if (viewport.width === 1440 && theme === "light" && accent === "blue") {
            await reviewedDesign(page);
            await interactions(page);
            await highlightGeometry(page);
            await focusMode(page);
            await hashAndPrint(page);
            await expandCards(page);
          }
          await screenshots(page, `${viewport.width}x${viewport.height}-${theme}-${accent}`);
        });
      }
    }

    await openPage(browser, {
      viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1
    }, async page => {
      await page.goto(`${url}?theme=dark&accent=green`, { waitUntil: "load" });
      await expandCards(page);
      check("390px layout has no body overflow or expanded mobile viewport", await page.evaluate(() =>
        innerWidth <= 391 && document.documentElement.scrollWidth <= 391));
      for (const id of diagramIds) check(`${id}: narrow diagram scrolls instead of shrinking labels`,
        await page.locator(`#${id} .diagram-scroll`).evaluate(element => element.scrollWidth > element.clientWidth + 100));
      const figure = page.locator("#diagram-infrastructure");
      const node = figure.locator(".arch-node").first();
      await node.tap();
      const popup = await visiblePopup(page, true);
      await page.setViewportSize({ width: 390, height: 360 });
      await frame(page);
      await popupBounds(page);
      check("pinned popup survives a short mobile viewport", await popup.isVisible());
      await page.setViewportSize({ width: 390, height: 844 });
      await frame(page);
      if (shots) await page.screenshot({ path: path.join(shots, "390x844-touch-pinned.png") });
      await popup.locator(".diagram-popup-close").tap();
      check("touch close dismisses popup", !await popup.isVisible());
      await figure.locator(".diagram-focus").tap();
      await node.tap();
      await visiblePopup(page, true);
      await popup.locator(".diagram-popup-close").tap();
      await figure.locator(".diagram-focus").tap();
      check("touch focus mode is reversible", !await figure.evaluate(element => element.classList.contains("is-focused")));
      check("narrow layout remains within body after interaction", await page.evaluate(() =>
        innerWidth <= 391 && document.documentElement.scrollWidth <= 391));
      if (shots) await page.screenshot({ path: path.join(shots, "390x844-touch.png") });
    });

    await openPage(browser, { viewport: { width: 1440, height: 900 }, javaScriptEnabled: false }, async page => {
      await page.goto(url, { waitUntil: "load" });
      check("no-JS focus controls are not misleading", !await page.locator(".diagram-focus").first().isVisible());
      for (const detail of await page.locator(".component-detail").all()) {
        check("authored reference is open without JavaScript", await detail.getAttribute("open") !== null);
        check("essential reference content is visible without JavaScript", await detail.locator(":scope > .component-copy").isVisible());
      }
      const node = page.locator(".arch-node").first();
      const id = await node.getAttribute("data-detail");
      await node.click();
      check("native SVG link works without JavaScript", decodeURIComponent(new URL(page.url()).hash.slice(1)) === id);
      check("native target remains readable", await page.locator(`#${id} > .component-copy`).isVisible());
      await page.emulateMedia({ media: "print" });
      check("no-JS print omits popup-only reference material", !await page.locator("[data-popup-reference]").isVisible());
      check("no-JS print retains the diagrams", await page.locator("#diagram-infrastructure").isVisible());
      await uniqueIds(page);
    });
    console.log(`architecture: ${checks} checks passed; 12 desktop palettes/viewports, touch, no-JS, print, offline${shots ? `; screenshots: ${shots}` : ""}`);
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
