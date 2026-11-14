import { defineConfig } from "@playwright/test";
import path from "node:path";

for (const name of ["TSIM_DEMO_E2E_URL", "TSIM_DEMO_E2E_EXPIRED_URL", "TSIM_DEMO_E2E_ACCESS_CODE",
  "TSIM_DEMO_E2E_EVIDENCE"]) {
  if (!process.env[name]) throw new Error(`Demo browser tests require ${name}. Use real active and expired demo servers.`);
}

export default defineConfig({
  testDir: ".",
  testMatch: "**/demo.spec.mjs",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: path.join(process.env.TSIM_DEMO_E2E_EVIDENCE, "demo-playwright"),
  reporter: [["line"], ["json", {
    outputFile: path.join(process.env.TSIM_DEMO_E2E_EVIDENCE, "demo-playwright-results.json"),
  }]],
  use: {
    baseURL: process.env.TSIM_DEMO_E2E_URL,
    channel: process.env.TSIM_DEMO_E2E_BROWSER || "msedge",
    headless: true,
    viewport: { width: 1366, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
