"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRequire } = require("node:module");

function playwright() {
  const skillValidator = path.join(os.homedir(), ".copilot", "skills", "html-docs", "assets", "validate.js");
  const resolvers = [require, createRequire(skillValidator)];
  const roots = [
    path.join(os.homedir(), "AppData", "Roaming", "npm", "node_modules"),
    path.join(os.homedir(), ".npm-global", "lib", "node_modules"),
    path.join(path.parse(process.execPath).root, "usr", "local", "lib", "node_modules")
  ];
  const candidates = [
    process.env.PLAYWRIGHT_MODULE, "playwright", "playwright-core",
    ...roots.flatMap(root => [
      path.join(root, "playwright"),
      path.join(root, "playwright-core"),
      path.join(root, "@playwright", "cli", "node_modules", "playwright")
    ])
  ].filter(Boolean);
  for (const resolve of resolvers) for (const candidate of candidates) {
    try { return resolve(candidate); }
    catch (error) { if (error.code !== "MODULE_NOT_FOUND") throw error; }
  }
  throw new Error("Playwright is missing. Set PLAYWRIGHT_MODULE to an existing installation; otherwise use the approved package feed.");
}

function browserPath() {
  if (process.env.PLAYWRIGHT_CHROMIUM) {
    assert.ok(fs.existsSync(process.env.PLAYWRIGHT_CHROMIUM), "PLAYWRIGHT_CHROMIUM must exist");
    return process.env.PLAYWRIGHT_CHROMIUM;
  }
  for (const root of [process.env["ProgramFiles(x86)"], process.env.ProgramFiles, process.env.LOCALAPPDATA].filter(Boolean)) {
    const edge = path.join(root, "Microsoft", "Edge", "Application", "msedge.exe");
    if (fs.existsSync(edge)) return edge;
  }
  for (const root of [
    path.join(os.homedir(), "AppData", "Local", "ms-playwright"),
    path.join(os.homedir(), ".cache", "ms-playwright"),
    path.join(os.homedir(), "Library", "Caches", "ms-playwright")
  ]) {
    if (!fs.existsSync(root)) continue;
    for (const directory of fs.readdirSync(root).filter(name => name.startsWith("chromium")).sort().reverse()) {
      for (const parts of [
        ["chrome-win64", "chrome.exe"], ["chrome-win", "chrome.exe"],
        ["chrome-linux", "chrome"], ["chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"]
      ]) {
        const file = path.join(root, directory, ...parts);
        if (fs.existsSync(file)) return file;
      }
    }
  }
  return undefined;
}

module.exports = { playwright, browserPath };
