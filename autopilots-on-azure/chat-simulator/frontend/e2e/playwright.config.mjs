import { defineConfig } from "@playwright/test";
import path from "node:path";

const evidence = process.env.TSIM_E2E_EVIDENCE;
if (!evidence || !process.env.TSIM_E2E_URL) {
  throw new Error("Run node tools\\run_browser_tests.mjs from chat-simulator.");
}

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.mjs",
  testIgnore: "**/demo.spec.mjs",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 5_000 },
  outputDir: path.join(evidence, "playwright"),
  reporter: [
    ["line"],
    ["json", { outputFile: path.join(evidence, "playwright-results.json") }],
  ],
  use: {
    channel: process.env.TSIM_E2E_BROWSER || "msedge",
    headless: true,
    baseURL: process.env.TSIM_E2E_URL,
    viewport: { width: 1366, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
