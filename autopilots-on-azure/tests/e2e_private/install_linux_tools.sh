#!/usr/bin/env bash
set -euo pipefail
/opt/private-runner-tools/bin/pip install --disable-pip-version-check \
  --index-url https://packagefeedproxy.microsoft.io/pypi/simple/ \
  'uv==0.13.7' 'azure-cli==2.92.0' > /tmp/private-python-tools.log
ln -sf /opt/private-runner-tools/bin/uv /usr/local/bin/uv
ln -sf /opt/private-runner-tools/bin/az /usr/local/bin/az
node_version="$(npm view 'node-linux-x64@24' version --json | python3 -c 'import json,sys; v=json.load(sys.stdin); print(v[-1] if isinstance(v,list) else v)')"
npm view "node-linux-x64@$node_version" version engines --json
npm install --prefix /opt/private-node --ignore-scripts --no-audit --no-fund "node-linux-x64@$node_version" > /tmp/private-node.log
ln -sf /opt/private-node/node_modules/node-linux-x64/bin/node /usr/local/bin/node
mkdir -p /opt/private-playwright
npm install --prefix /opt/private-playwright --no-audit --no-fund '@playwright/test@1.64.1' > /tmp/private-playwright.log
/usr/local/bin/node /opt/private-playwright/node_modules/playwright/cli.js install --with-deps chromium > /tmp/private-chromium.log
terraform_version=1.15.8
work="$(mktemp -d)"
trap 'rm -f "$work/terraform.zip" "$work/sums" "$work/terraform" "$work/LICENSE.txt"; rmdir "$work"' EXIT
curl -fsS "https://releases.hashicorp.com/terraform/$terraform_version/terraform_${terraform_version}_linux_amd64.zip" -o "$work/terraform.zip"
curl -fsS "https://releases.hashicorp.com/terraform/$terraform_version/terraform_${terraform_version}_SHA256SUMS" -o "$work/sums"
expected="$(awk '/linux_amd64.zip$/ {print $1}' "$work/sums")"
printf '%s  %s\n' "$expected" "$work/terraform.zip" | sha256sum --check
unzip -q "$work/terraform.zip" -d "$work"
install -m 755 "$work/terraform" /usr/local/bin/terraform
node --version
uv --version
az version --query '"azure-cli"' -o tsv
terraform version -json
az login --identity --allow-no-subscriptions --output none
az account show --query '{subscription:name,identityType:user.type}' -o json
PRIVATE_PLAYWRIGHT_MODULE=/opt/private-playwright/node_modules/@playwright/test \
node -e 'const {chromium}=require(process.env.PRIVATE_PLAYWRIGHT_MODULE); (async()=>{const b=await chromium.launch({headless:true});const p=await b.newPage();await p.goto("data:text/html,<h1>Private client ready</h1>");console.log(await p.locator("h1").textContent());await b.close()})().catch(e=>{console.error(e.message);process.exitCode=1})'
