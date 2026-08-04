"""Capture Microsoft Foundry portal screenshots for the demo guide.

Uses the locally installed Microsoft Edge through a dedicated persistent profile so
the portal session survives between runs. Sign in once with ``--login``; every later
run reuses the stored session.
"""

from __future__ import annotations

import argparse
import time
import tomllib
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
DEFAULT_SHOTS = HERE / "shots.toml"
DEFAULT_PROFILE = HERE / ".profile"
DEFAULT_OUTPUT = HERE.parent / "images"


def build_base_url(config: dict[str, Any]) -> str:
    portal = config["portal"]
    return f"{portal['host'].rstrip('/')}/nextgen/r/{portal['workspace_key']}"


def resolve_url(base_url: str, config: dict[str, Any], url: str) -> str:
    resolved = url if url.startswith("http") else base_url + url
    tenant_id = config["portal"].get("tenant_id")
    if tenant_id and "tid=" not in resolved:
        separator = "&" if "?" in resolved else "?"
        resolved = f"{resolved}{separator}tid={tenant_id}"
    return resolved


def settle(page: Page, shot: dict[str, Any], default_settle_ms: int) -> None:
    for selector in shot.get("wait_for", []):
        page.wait_for_selector(selector, timeout=shot.get("timeout_ms", 60000))
    page.wait_for_timeout(shot.get("settle_ms", default_settle_ms))


def apply_actions(page: Page, shot: dict[str, Any]) -> None:
    for action in shot.get("actions", []):
        kind = action["type"]
        if kind == "click":
            page.click(action["selector"], timeout=action.get("timeout_ms", 30000))
        elif kind == "click_text":
            page.get_by_text(
                action["text"], exact=action.get("exact", False)
            ).first.click(timeout=action.get("timeout_ms", 30000))
        elif kind == "fill":
            page.fill(action["selector"], action["value"])
        elif kind == "press":
            page.keyboard.press(action["key"])
        elif kind == "wait":
            page.wait_for_timeout(action["ms"])
        elif kind == "scroll":
            page.mouse.move(action.get("x", 900), action.get("y", 600))
            page.mouse.wheel(0, action["dy"])
            page.wait_for_timeout(action.get("settle_ms", 1500))
        else:
            raise ValueError(f"Unknown action type: {kind}")


def capture(
    page: Page,
    base_url: str,
    config: dict[str, Any],
    shot: dict[str, Any],
    output_dir: Path,
    default_settle_ms: int,
) -> Path:
    page.goto(
        resolve_url(base_url, config, shot["url"]),
        wait_until="domcontentloaded",
        timeout=90000,
    )
    settle(page, shot, default_settle_ms)
    apply_actions(page, shot)
    if shot.get("actions"):
        page.wait_for_timeout(shot.get("post_action_settle_ms", default_settle_ms))
    target = output_dir / f"{shot['id']}.png"
    if clip_selector := shot.get("clip_selector"):
        page.locator(clip_selector).first.screenshot(path=target)
    else:
        page.screenshot(path=target, full_page=shot.get("full_page", False))
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, default=DEFAULT_SHOTS)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only", nargs="*", help="Capture only these shot ids.")
    parser.add_argument(
        "--login", action="store_true", help="Open the portal and wait for sign-in."
    )
    parser.add_argument("--login-timeout", type=int, default=600)
    args = parser.parse_args()

    config = tomllib.loads(args.shots.read_text(encoding="utf-8"))
    base_url = build_base_url(config)
    default_settle_ms = config["portal"].get("settle_ms", 4000)
    viewport = {
        "width": config["portal"].get("width", 1680),
        "height": config["portal"].get("height", 1000),
    }

    args.profile.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(args.profile),
            channel="msedge",
            headless=False,
            viewport=viewport,
            args=["--disable-features=msEdgeSplitScreen,msSidebar"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        if args.login:
            page.goto(
                resolve_url(base_url, config, config["portal"]["login_url"]),
                wait_until="domcontentloaded",
                timeout=90000,
            )
            print("Sign in to the Foundry portal in the window that just opened.")
            print(f"Use the account that owns the project: {config['portal']['account_hint']}")
            deadline = time.monotonic() + args.login_timeout
            while time.monotonic() < deadline:
                page.wait_for_timeout(3000)
                body = page.inner_text("body") if page.url.startswith("http") else ""
                if "No access" in body or "Page not found" in body:
                    continue
                if config["portal"]["ready_text"] in body:
                    break
            else:
                print("Timed out waiting for sign-in.")
                context.close()
                return
            page.wait_for_timeout(4000)
            probe = args.output / "_login-probe.png"
            page.screenshot(path=probe)
            print(f"Signed in. Session stored in {args.profile}")
            print(f"Probe screenshot: {probe}")
            context.close()
            return

        shots = config["shot"]
        if args.only:
            wanted = set(args.only)
            shots = [shot for shot in shots if shot["id"] in wanted]
        for shot in shots:
            try:
                target = capture(
                    page, base_url, config, shot, args.output, default_settle_ms
                )
                print(f"captured {shot['id']} -> {target}")
            except Exception as exc:
                print(f"FAILED {shot['id']}: {type(exc).__name__}: {exc}")
        context.close()


if __name__ == "__main__":
    main()
