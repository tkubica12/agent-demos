import { mkdir, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { metric } from "./helpers.mjs";

export async function captureOwnerClip(page) {
  const encoder = spawnSync("ffmpeg", ["-version"], { stdio: "ignore", windowsHide: true });
  if (encoder.status !== 0) {
    await metric("optional-owner-clip", { recorded: false, reason: "No already-installed ffmpeg encoder." });
    return;
  }
  const directory = path.resolve(import.meta.dirname, "..", "..", ".recordings", process.env.TSIM_E2E_RUN);
  const frames = path.join(directory, "owner-frames");
  await mkdir(frames, { recursive: true });
  const output = path.join(directory, "owner-send-emoji.mp4");
  const started = Date.now();
  let count = 0;
  try {
    // Recording begins only after authenticated team creation. No auth screens or network frames.
    while (Date.now() - started < 10_000) {
      await page.screenshot({ path: path.join(frames, `frame-${String(count++).padStart(4, "0")}.jpg`),
        type: "jpeg", quality: 65 });
      await new Promise((resolve) => setTimeout(resolve, 125));
    }
    const args = ["-hide_banner", "-loglevel", "error", "-y",
      "-framerate", String(count / 10), "-i", path.join(frames, "frame-%04d.jpg"), "-t", "10",
      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", output];
    const encoded = spawnSync("ffmpeg", args, { windowsHide: true, encoding: "utf8", timeout: 20_000 });
    await metric("optional-owner-clip", { recorded: encoded.status === 0, path: output,
      seconds: 10, frames: count, authenticationScreens: false,
      ...(process.env.TSIM_E2E_HOSTED_RUN ? { authentication: "real-entra", actorAlias: "owner" }
        : { syntheticIdentity: "owner" }),
      encoderCommand: { command: "ffmpeg", args }, availabilityProbe: { command: "ffmpeg", args: ["-version"] },
      ...(encoded.status !== 0 ? { reason: "Existing encoder failed; optional clip only." } : {}) });
  } catch {
    await metric("optional-owner-clip", { recorded: false, reason: "Browser closed before optional recording finished." });
  } finally {
    await rm(frames, { recursive: true, force: true });
  }
}
