#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv nodejs npm unzip curl > /tmp/private-runner-packages.log
python3 -m venv /opt/private-runner-tools
/opt/private-runner-tools/bin/pip config debug
/opt/private-runner-tools/bin/pip index versions uv --index-url https://packagefeedproxy.microsoft.io/pypi/simple/ | sed -n '1p'
/opt/private-runner-tools/bin/pip index versions azure-cli --index-url https://packagefeedproxy.microsoft.io/pypi/simple/ | sed -n '1p'
npm config get registry
npm config set registry https://packagefeedproxy.microsoft.io/npm/
npm view @playwright/test@1.64.1 version engines --json
