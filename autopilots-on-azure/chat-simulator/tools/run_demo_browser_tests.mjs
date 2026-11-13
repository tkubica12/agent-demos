import { spawn } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const run = `demo-e2e-${new Date().toISOString().replaceAll(/[:.]/g, "-")}-${randomUUID().slice(0, 6)}`;
const cache = path.join(root, ".cache", run);
const evidence = path.join(root, "test-results", run);
await mkdir(cache, { recursive: true });
await mkdir(evidence, { recursive: true });
const ports = [Number(process.env.TSIM_DEMO_E2E_PORT || 8041),
  Number(process.env.TSIM_DEMO_E2E_EXPIRING_PORT || 8042)];
if (new Set(ports).size !== 2 || ports.some((port) =>
  !Number.isInteger(port) || port < 1024 || port > 65535 || [8000, 8001, 8002, 5173].includes(port))) {
  throw new Error("Demo E2E needs two distinct non-reserved loopback ports.");
}
const [url, expiredUrl] = ports.map((port) => `http://127.0.0.1:${port}`);
const accessCode = randomBytes(32).toString("base64url");
const children = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const env = {
  ...process.env, ENV: "development", TSIM_AUTH_MODE: "demo",
  TSIM_DEMO_ACCESS_CODE: accessCode, TSIM_DEMO_ACKNOWLEDGE: "",
  TSIM_TEST_IDP: "0", TSIM_TENANT_ID: "", TSIM_CLIENT_ID: "", TSIM_SCOPE: "",
  TSIM_CHANNEL_CLIENT_ID: "", TSIM_CHANNEL_CLIENT_SECRET: "", TSIM_CHANNEL_MANAGED_IDENTITY_CLIENT_ID: "",
  TSIM_AGENTS_JSON: "", TSIM_AGENTS_FILE: "",
  TSIM_DEMO_E2E_URL: url, TSIM_DEMO_E2E_EXPIRED_URL: expiredUrl,
  TSIM_DEMO_E2E_ACCESS_CODE: accessCode, TSIM_DEMO_E2E_EVIDENCE: evidence,
};

function start(command, args, childEnv) {
  const child = spawn(command, args, { cwd: root, env: childEnv, windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"] });
  children.push(child);
  for (const stream of [child.stdout, child.stderr]) {
    stream.on("data", (chunk) => process.stdout.write(chunk.toString().replaceAll(accessCode, "[REDACTED_CODE]")));
  }
  return child;
}

function completion(child) {
  return new Promise((resolve, reject) => {
    if (child.exitCode !== null) { resolve(child.exitCode); return; }
    child.once("error", reject);
    child.once("exit", (code) => resolve(code ?? 1));
  });
}

async function stop(child) {
  if (child.exitCode !== null) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"],
      { stdio: "ignore", windowsHide: true });
    await completion(killer);
  } else child.kill("SIGTERM");
  await Promise.race([completion(child), sleep(3_000)]);
}

async function ready(origin, child, status = 200) {
  for (let attempt = 0; attempt < 160; attempt++) {
    if (child.exitCode !== null) throw new Error("Owned demo helper exited before readiness.");
    try {
      if ((await fetch(`${origin}/api/health`)).status === status) return;
    } catch {}
    await sleep(250);
  }
  throw new Error(`Demo helper readiness timed out (${status}).`);
}

async function available(port) {
  const listener = createServer();
  await new Promise((resolve, reject) => {
    listener.once("error", reject);
    listener.listen(port, "127.0.0.1", resolve);
  });
  await new Promise((resolve) => listener.close(resolve));
}

function backend(port, expiry) {
  const origin = `http://127.0.0.1:${port}`;
  const directory = path.join(cache, String(port));
  return start("uv", ["run", "--locked", "python", "-m", "uvicorn", "backend.app:create_app",
    "--factory", "--host", "127.0.0.1", "--port", String(port), "--no-access-log", "--log-level", "warning"], {
    ...env, TSIM_DEMO_EXPIRES_AT: expiry, TSIM_SERVICE_URL: origin,
    TSIM_REDIRECT_URI: `${origin}/`, TSIM_ALLOWED_ORIGINS: origin,
    TSIM_DATABASE_URL: `sqlite:///${path.join(directory, "demo.db").replaceAll("\\", "/")}`,
    TSIM_STORAGE_PATH: path.join(directory, "files"),
  });
}

const summary = { run, url, expiredUrl, evidence, startedAt: new Date().toISOString() };
let exitCode = 1;
try {
  await Promise.all(ports.map(available));
  const active = backend(ports[0], new Date(Date.now() + 20 * 60_000).toISOString());
  const expiring = backend(ports[1], new Date(Date.now() + 12_000).toISOString());
  await Promise.all([ready(url, active), ready(expiredUrl, expiring)]);
  const html = await (await fetch(url)).text();
  if (!html.includes("/assets/")) throw new Error("Build frontend\\dist before running demo browser tests.");
  await ready(expiredUrl, expiring, 410);
  const config = await fetch(`${expiredUrl}/api/config`);
  if (config.status !== 410 || (await config.json()).authMode !== "demo") {
    throw new Error("Expired demo must refuse configuration with warning metadata.");
  }
  const browser = start(process.execPath, [
    path.join(root, "frontend", "node_modules", "@playwright", "test", "cli.js"), "test",
    "--config", path.join(root, "frontend", "e2e", "demo.playwright.config.mjs"),
  ], env);
  exitCode = await completion(browser);
} catch (error) {
  summary.error = error.message;
  console.error(error.message);
} finally {
  await Promise.all(children.map(stop));
  summary.exitCode = exitCode;
  summary.finishedAt = new Date().toISOString();
  await writeFile(path.join(evidence, "runner-summary.json"), JSON.stringify(summary, null, 2));
  console.log(`Stopped owned demo helpers. Results: ${evidence}`);
  process.exitCode = exitCode;
}
