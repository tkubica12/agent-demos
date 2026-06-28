from __future__ import annotations

import shutil
import subprocess
import sys


def main() -> None:
    node = shutil.which("node")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    openclaw = shutil.which("openclaw") or shutil.which("openclaw.cmd")
    print(f"node={node or 'missing'}")
    print(f"npm={npm or 'missing'}")
    print(f"openclaw={openclaw or 'missing'}")
    ok = True
    if node:
        node_result = subprocess.run([node, "--version"], check=False)
        ok = ok and node_result.returncode == 0
    if openclaw:
        openclaw_result = subprocess.run([openclaw, "--version"], check=False)
        ok = ok and openclaw_result.returncode == 0
    else:
        print("Install OpenClaw with: npm install -g openclaw@latest")
        ok = False
    if not node or not npm:
        ok = False
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
