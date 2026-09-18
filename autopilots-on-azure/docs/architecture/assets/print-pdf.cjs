#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { playwright, browserPath } = require("./browser-support.cjs");

const args = process.argv.slice(2);
if (args.length < 1 || args.length > 2 || !/\.html$/i.test(args[0])) {
  console.error("Usage: node assets\\print-pdf.cjs document.standalone.html [output.pdf]");
  process.exit(2);
}
const input = path.resolve(args[0]);
const output = path.resolve(args[1] || input.replace(/(?:\.standalone)?\.html$/i, ".pdf"));
assert.ok(fs.existsSync(input), `HTML document exists: ${input}`);
assert.ok(/\.pdf$/i.test(output), "Output must be a PDF file.");

(async () => {
  const browser = await playwright().chromium.launch({ executablePath: browserPath(), headless: true });
  try {
    const context = await browser.newContext({
      offline: true,
      reducedMotion: "reduce",
      viewport: { width: 1123, height: 1587 }
    });
    const page = await context.newPage();
    const problems = [];
    page.on("pageerror", error => problems.push(error.message));
    page.on("console", message => { if (message.type() === "error") problems.push(message.text()); });
    page.on("requestfailed", request => problems.push(`Failed request: ${request.url()}`));
    page.on("request", request => {
      if (/^https?:/i.test(request.url())) problems.push(`External print dependency: ${request.url()}`);
    });
    await page.goto(pathToFileURL(input).href + "?theme=dark&accent=blue", { waitUntil: "load", timeout: 60000 });
    await page.emulateMedia({ media: "print" });
    await page.evaluate(async () => {
      window.dispatchEvent(new Event("beforeprint"));
      await document.fonts.ready;
    });
    assert.equal(await page.locator("html").getAttribute("data-theme"), "light", "Print must use a light background.");
    assert.equal(await page.locator(".architecture-svg:visible").count(), 4, "All four diagrams must print.");
    assert.equal(await page.locator(".controls:visible, .diagram-focus:visible, .diagram-popup:visible, [data-popup-reference]:visible").count(),
      0, "Print must omit controls and popup-only material.");
    const pdf = await page.pdf({
      preferCSSPageSize: true,
      printBackground: true,
      displayHeaderFooter: false,
      tagged: true,
      outline: true
    });
    await page.evaluate(() => window.dispatchEvent(new Event("afterprint")));
    assert.equal(await page.locator("html").getAttribute("data-theme"), "dark", "Print must restore the reading theme.");
    assert.deepEqual(problems, [], "PDF rendering must work offline without browser errors.");
    fs.writeFileSync(output, pdf);
    console.log(`${output} / A3 portrait / ${(pdf.length / 1024).toFixed(0)} KB`);
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
