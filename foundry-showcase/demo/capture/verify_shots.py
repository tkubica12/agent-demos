"""Check every shot URL in shots.toml actually renders content.

Reports pages that 404, deny access, or come back essentially empty, so broken
deep links are caught before the screenshots are used.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BAD_MARKERS = ("Page not found", "No access", "Something went wrong")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, default=HERE / "shots.toml")
    parser.add_argument("--profile", type=Path, default=HERE / ".profile")
    parser.add_argument("--settle-ms", type=int, default=9000)
    args = parser.parse_args()

    config = tomllib.loads(args.shots.read_text(encoding="utf-8"))
    portal = config["portal"]
    base_url = f"{portal['host'].rstrip('/')}/nextgen/r/{portal['workspace_key']}"
    tenant_id = portal.get("tenant_id", "")

    failures: list[str] = []
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(args.profile),
            channel="msedge",
            headless=False,
            viewport={"width": 1680, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        for shot in config["shot"]:
            url = shot["url"]
            url = url if url.startswith("http") else base_url + url
            if tenant_id and "tid=" not in url:
                url += ("&" if "?" in url else "?") + f"tid={tenant_id}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(args.settle_ms)
                body = page.inner_text("body")
                bad = next((m for m in BAD_MARKERS if m in body), None)
                if bad:
                    failures.append(shot["id"])
                    print(f"BROKEN  {shot['id']:<28} {bad}")
                elif len(body.strip()) < 200:
                    failures.append(shot["id"])
                    print(f"EMPTY   {shot['id']:<28} {len(body.strip())} chars")
                else:
                    heading = body.strip().splitlines()
                    label = next((h for h in heading if len(h.strip()) > 2), "")[:60]
                    print(f"ok      {shot['id']:<28} {label}")
            except Exception as exc:
                failures.append(shot["id"])
                print(f"ERROR   {shot['id']:<28} {type(exc).__name__}: {exc}")
        context.close()

    print(f"\n{len(failures)} problem(s): {failures}" if failures else "\nall shot URLs render")


if __name__ == "__main__":
    main()
