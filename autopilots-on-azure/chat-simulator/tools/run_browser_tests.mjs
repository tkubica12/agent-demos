import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { createServer as createSocketServer } from "node:net";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const run = `collaboration-e2e-${new Date().toISOString().replaceAll(/[:.]/g, "-")}-${randomUUID().slice(0, 6)}`;
const cache = path.join(root, ".cache", "e2e", run);
const evidence = path.join(root, "test-results", run);
await mkdir(cache, { recursive: true });
await mkdir(evidence, { recursive: true });
const backendPort = Number(process.env.TSIM_E2E_BACKEND_PORT || 8011);
const idpPort = Number(process.env.TSIM_E2E_IDP_PORT || 8012);
const controlPort = Number(process.env.TSIM_E2E_CONTROL_PORT || 8013);
if (new Set([backendPort, idpPort, controlPort]).size !== 3 ||
    [backendPort, idpPort, controlPort].some((port) => [8000, 8001, 8002, 5173].includes(port))) {
  throw new Error("E2E ports must be distinct and must not use parent service ports.");
}
const url = `http://127.0.0.1:${backendPort}`;
const key = randomUUID();
const env = {
  ...process.env,
  ENV: "development",
  TSIM_AUTH_MODE: "entra",
  TSIM_AGENTS_JSON: "",
  TSIM_AGENTS_FILE: "",
  TSIM_TEST_IDP: "1",
  TSIM_TENANT_ID: "11111111-1111-4111-8111-111111111111",
  TSIM_CLIENT_ID: "22222222-2222-4222-8222-222222222222",
  TSIM_SCOPE: "api://22222222-2222-4222-8222-222222222222/access_as_user",
  TSIM_CHANNEL_ID: "msteams",
  TSIM_CHANNEL_CLIENT_ID: "",
  TSIM_CHANNEL_CLIENT_SECRET: "",
  TSIM_CHANNEL_MANAGED_IDENTITY_CLIENT_ID: "",
  TSIM_E2E_HOSTED_RUN: "",
  TSIM_TEST_ISSUER: `http://127.0.0.1:${idpPort}`,
  TSIM_ALLOWED_ORIGINS: url,
  TSIM_DATABASE_URL: `sqlite:///${path.join(cache, "collaboration.db").replaceAll("\\", "/")}`,
  TSIM_STORAGE_PATH: path.join(cache, "files"),
  TSIM_E2E_DATABASE: path.join(cache, "collaboration.db"),
  TSIM_E2E_URL: url,
  TSIM_E2E_IDP: `http://127.0.0.1:${idpPort}`,
  TSIM_E2E_CONTROL: `http://127.0.0.1:${controlPort}`,
  TSIM_E2E_CONTROL_KEY: key,
  TSIM_E2E_EVIDENCE: evidence,
  TSIM_E2E_RUN: run,
};
const children = new Set();
const log = [];
const commandSequence = [];
let backend;
let closing = false;
let restarts = 0;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function note(text) {
  log.push(`${text}\n`);
  console.log(text);
}
function start(command, args) {
  const invocation = { command, args, cwd: root };
  commandSequence.push(invocation);
  note(`[command] ${JSON.stringify(invocation)}`);
  const child = spawn(command, args, { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  children.add(child);
  child.on("error", () => children.delete(child));
  child.once("exit", () => children.delete(child));
  // No access logs, HTTP frames, browser traces, response bodies, or bearer tokens are recorded.
  for (const stream of [child.stdout, child.stderr]) {
    stream.on("data", (chunk) => {
      const text = chunk.toString().replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[REDACTED_JWT]")
        .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]");
      log.push(text);
      process.stdout.write(text);
    });
  }
  return child;
}
function completion(child) {
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => resolve(code ?? 1));
  });
}
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
    await completion(killer);
  } else child.kill("SIGTERM");
  await Promise.race([completion(child), sleep(3_000)]);
}
async function ready(target, child) {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (child.exitCode !== null) throw new Error("Owned helper exited before readiness.");
    try { if ((await fetch(target)).ok) return; } catch {}
    await sleep(250);
  }
  throw new Error(`Owned helper readiness timed out: ${target}`);
}
async function assertPortAvailable(port) {
  const listener = createSocketServer();
  await new Promise((resolve, reject) => {
    listener.once("error", reject);
    listener.listen(port, "127.0.0.1", resolve);
  });
  await new Promise((resolve) => listener.close(resolve));
}
async function readyBuild() {
  let previous = "";
  let stable = 0;
  for (let attempt = 0; attempt < 120; attempt++) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error("Build entry not ready");
      const html = await response.text();
      const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((match) => match[1]);
      if (!assets.length) throw new Error("No built app assets");
      const content = await Promise.all(assets.map(async (asset) => {
        const resource = await fetch(`${url}${asset}`);
        if (!resource.ok) throw new Error("Build asset not ready");
        return Buffer.from(await resource.arrayBuffer());
      }));
      const hash = createHash("sha256").update(html);
      for (const asset of content) hash.update(asset);
      const fingerprint = hash.digest("hex");
      stable = fingerprint === previous ? stable + 1 : 0;
      previous = fingerprint;
      if (stable >= 3) return { fingerprint, assets };
    } catch { stable = 0; }
    await sleep(500);
  }
  throw new Error("Built frontend and assets did not become stable.");
}
async function startBackend() {
  backend = start("uv", ["run", "--locked", "python", "-m", "uvicorn", "backend.app:create_app",
    "--factory", "--host", "127.0.0.1", "--port", String(backendPort), "--no-access-log", "--log-level", "warning"]);
  await ready(`${url}/api/health`, backend);
}
const control = createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/restart" || req.headers["x-e2e-key"] !== key || closing) {
    res.writeHead(403).end(); return;
  }
  try {
    await stop(backend);
    await startBackend();
    restarts++;
    res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ restarted: true, restarts }));
  } catch {
    res.writeHead(500).end("Owned backend restart failed.");
  }
});
let exitCode = 1;
const summary = { run, auth: "local synthetic OIDC; NOT real tenant authentication", evidence, cache,
  startedAt: new Date().toISOString(), backendRestarts: 0 };
try {
  await assertPortAvailable(backendPort);
  await assertPortAvailable(idpPort);
  await new Promise((resolve, reject) => {
    control.once("error", reject);
    control.listen(controlPort, "127.0.0.1", resolve);
  });
  const idp = start("uv", ["run", "--locked", "python", "-m", "tests.test_idp"]);
  await ready(`${env.TSIM_E2E_IDP}/.well-known/openid-configuration`, idp);
  await startBackend();
  note(`E2E evidence: ${evidence}`);
  const unit = start("uv", ["run", "--locked", "python", "-m", "pytest", "-q", "-o", "addopts=",
    `--basetemp=${path.join(cache, "pytest-work")}`]);
  summary.apiExitCode = await completion(unit);
  summary.testedBuild = await readyBuild();
  const browser = start(process.execPath, [path.join(root, "frontend", "node_modules", "@playwright", "test", "cli.js"),
    "test", "--config", path.join(root, "frontend", "e2e", "playwright.config.mjs")]);
  summary.browserExitCode = await completion(browser);
  const report = JSON.parse(await readFile(path.join(evidence, "playwright-results.json"), "utf8"));
  summary.browserStats = report.stats;
  summary.finishedBuild = await readyBuild();
  summary.buildChangedDuringRun = summary.testedBuild.fingerprint !== summary.finishedBuild.fingerprint;
  exitCode = summary.apiExitCode === 0 && summary.browserExitCode === 0 && !summary.buildChangedDuringRun &&
    report.stats.skipped === 0 && report.stats.unexpected === 0 && report.stats.expected >= 9 ? 0 : 1;
} catch (error) {
  note(error.message);
  summary.error = error.message;
} finally {
  closing = true;
  await Promise.all([...children].map(stop));
  await new Promise((resolve) => control.close(resolve));
  summary.backendRestarts = restarts;
  summary.finishedAt = new Date().toISOString();
  summary.exitCode = exitCode;
  const environment = Object.fromEntries([
    "ENV", "TSIM_TEST_IDP", "TSIM_TEST_ISSUER", "TSIM_ALLOWED_ORIGINS", "TSIM_DATABASE_URL",
    "TSIM_STORAGE_PATH", "TSIM_E2E_DATABASE", "TSIM_E2E_URL", "TSIM_E2E_IDP", "TSIM_E2E_CONTROL",
    "TSIM_E2E_EVIDENCE", "TSIM_E2E_RUN",
  ].map((name) => [name, env[name]]));
  await writeFile(path.join(evidence, "runner-contract.json"), JSON.stringify({
    entryCommand: "node tools\\run_browser_tests.mjs",
    cwd: root,
    prerequisites: [
      "Built frontend\\dist; the runner never builds or installs dependencies.",
      "Existing uv.lock-compatible Python environment, existing frontend\\node_modules and system Edge (msedge).",
      "Free, distinct loopback ports; no rebuild of frontend\\dist during this run.",
      "Optional 10-second synthetic UI clip uses an already-installed ffmpeg; unavailable encoding is not a criterion failure.",
    ],
    portOverrides: { TSIM_E2E_BACKEND_PORT: backendPort, TSIM_E2E_IDP_PORT: idpPort,
      TSIM_E2E_CONTROL_PORT: controlPort, forbidden: [8000, 8001, 8002, 5173] },
    childEnvironment: environment,
    syntheticPublicConfiguration: [
      "TSIM_TENANT_ID", "TSIM_CLIENT_ID", "TSIM_SCOPE", "TSIM_REDIRECT_URI", "TSIM_CHANNEL_ID",
      "Test-only IDs are explicitly supplied by this runner; this suite expects channelId=msteams.",
    ],
    controlCredential: {
      variable: "TSIM_E2E_CONTROL_KEY", source: "Fresh random UUID, process memory only.",
      persisted: false, purpose: "Authenticated POST /restart on the owned loopback control server.",
    },
    commandSequence,
    nestedCommandEvidence: ["activity-validator-command.json", "optional-owner-clip.json"],
    execution: [
      "Start loopback restart-control HTTP server; verify ports are unused.",
      "Start explicit local test IdP and backend, verify their readiness.",
      "Run all pytest unit/API tests, then verify the built frontend/assets are stable.",
      "Run Playwright in system Edge: one worker, zero retries, zero skipped tests; C04 restarts only the owned backend.",
      "C08 invokes the locked Python Activity validator over the full API export and exact owned SQLite revision set.",
      "Fail on any API/browser failure, skipped browser test, missing expected scenario or build fingerprint change.",
      "Stop owned process trees and control listener; persist evidence even on failure. Parent process environment is unchanged.",
    ],
  }, null, 2));
  note(`Stopped all owned helpers. Results: ${evidence}`);
  await writeFile(path.join(evidence, "runner-summary.json"), JSON.stringify(summary, null, 2));
  await writeFile(path.join(evidence, "command-output.txt"), log.join(""));
  process.exitCode = exitCode;
}
