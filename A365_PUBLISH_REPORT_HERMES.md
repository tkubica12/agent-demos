# A365 Publish Report: Hermes Foundry Gateway

## Outcome

Hermes was published into the `tomasonline.net` Microsoft 365 / Teams tenant through a custom-engine Teams/A365 app that bridges to the existing Microsoft Foundry Hosted Agent.

Working assets:

| Area | Value |
|---|---|
| Tenant | `6ce4f237-667f-43f5-aafd-cbef954adf97` (`tomasonline.net`) |
| Azure subscription | `tokubica` / `673af34d-6b28-41dc-bc7b-f507418045e6` |
| Foundry project endpoint | `https://tomaskubica-foundry-resource.services.ai.azure.com/api/projects/tomaskubica-foundry-project` |
| Foundry Hosted Agent | `hermes-foundry-gateway` |
| Latest Hosted Agent version validated | `9` |
| A365 bridge App Service | `apphermesa365.azurewebsites.net` |
| Bridge managed identity client ID | `dc24aa91-7af1-4b77-a003-9418dd3d0d67` |
| Bridge managed identity principal ID | `a7b1998a-504e-493b-a1d4-61b33e14e4a5` |
| Teams catalog app ID | `dcc85d42-a0c3-47d0-8328-757375273b85` |
| Teams manifest external ID | `74fc3705-eaa4-4a82-9585-4a4d1afa6d7a` |
| Teams app display name | `hermes-foundry-a365dev` |
| Installed for user | `tomas@tomasonline.net` |

## Architecture published

```text
Teams / A365 custom engine app
        |
        v
Azure Bot Service registration
        |
        v
Azure App Service: apphermesa365
        |
        v
Microsoft Foundry project Responses API
        |
        v
Foundry Hosted Agent: hermes-foundry-gateway
        |
        v
Hermes runtime -> Azure Foundry model deployment
```

The generated `hermes-foundry-a365` project came from the Microsoft 365 Agents Toolkit `foundry-agent-to-m365` template. The template creates a custom-engine agent with a Bot Framework endpoint that calls a Foundry agent by name using the Responses API.

## Key challenges and fixes

### 1. Wrong tenant/account assumption

Initial assumption was that Microsoft 365/Teams publishing should use the Microsoft work account. That was wrong. Publishing had to happen in the `tomasonline.net` tenant.

Correct split:

| Scope | Account / tenant |
|---|---|
| Azure / Foundry | `tomas@tomasonline.net`, tenant `6ce4f237-667f-43f5-aafd-cbef954adf97` |
| Microsoft 365 / Teams publish | `admin@tomasonline.net`, same tenant |
| Target install/test user | `tomas@tomasonline.net` |

### 2. Toolkit browser auth was ambiguous and brittle

The Microsoft 365 Agents Toolkit (`atk` / `teamsapp`) repeatedly used browser/native-broker auth. With multiple signed-in identities this was hard to reason about, and the Toolkit token cache became corrupt or empty:

```text
C:\Users\tokubica\.fx\account\token.cache.appStudio.json
```

Provisioning through the Toolkit then hung silently or failed with native broker/token-cache errors.

Resolution: stop relying on Toolkit provision/publish workflow for the critical path. Use the Toolkit only to scaffold the `foundry-agent-to-m365` project, then execute provisioning and publishing manually with Azure CLI, Microsoft 365 CLI, Graph, and Teams PowerShell.

### 3. Foundry portal Playground needed streaming support

The Hosted Agent initially worked via CLI but Foundry portal Playground could show “running” without rendering output. The adapter returned normal JSON but did not support streaming and initially lacked a top-level `output_text` convenience field.

Fixes in `hermes-foundry/main.py`:

- Added top-level `output_text`.
- Added support for `stream=true`.
- Returned `text/event-stream` with Responses-style events:
  - `response.created`
  - `response.output_item.added`
  - `response.content_part.added`
  - `response.output_text.delta`
  - `response.output_text.done`
  - `response.completed`
  - `[DONE]`

CLI validation showed:

```text
Content-Type: text/event-stream; charset=utf-8
response.output_text.delta
data: [DONE]
```

### 4. Hosted Agent managed identity lacked model data-plane access

The Foundry Hosted Agent initially returned Hermes output containing Azure OpenAI 401 errors:

```text
Principal lacks Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action
```

Resolution: assign `Cognitive Services OpenAI User` to the Hosted Agent managed identity on the Foundry account.

### 5. M365 publish account had no license

Publishing with `tomas@tomasonline.net` authenticated correctly but failed:

```text
Failed to get license information for the user. Ensure user has a valid Office365 license assigned to them.
```

The tenant had available SKUs:

- `Microsoft_365_E5_(no_Teams)`
- `Microsoft_Teams_Enterprise_New`
- `Microsoft_365_Copilot`

But `tomas@tomasonline.net` had no assigned license and could not assign licenses due insufficient Graph privileges.

Resolution: use `admin@tomasonline.net`, which had Global Administrator role and relevant licenses.

### 6. Microsoft 365 CLI required explicit app registration and scopes

CLI for Microsoft 365 v11 required an explicit app ID:

```text
appId: appId is required
```

Using the public/default app ID authenticated, but app catalog publish failed due missing app catalog scopes:

```text
Missing scope permissions on the request.
API requires one of 'AppCatalog.Submit, AppCatalog.ReadWrite.All, Directory.ReadWrite.All'
```

Resolution:

1. Create a dedicated CLI app registration:

   ```powershell
   m365 cli app add --name "Hermes M365 CLI Publisher" --scopes all --saveToConfig
   ```

2. Resulting app ID:

   ```text
   1eff589c-4f63-48b0-a27e-d8a5f6a7d05d
   ```

3. Reconsent:

   ```powershell
   m365 cli app reconsent
   ```

4. Grant admin consent in the `tomasonline.net` tenant.

5. Relogin with device code using the dedicated app.

### 7. Teams app permission policy blocked install

Publishing succeeded, but installing for `tomas@tomasonline.net` initially failed:

```text
App is blocked by app permission policy.
AppType: Private
```

MicrosoftTeams PowerShell was installed and used to inspect/update policy. After consenting the dedicated CLI app and retrying, install succeeded.

### 8. Generated Toolkit env file needed cleanup

The generated environment file was patched manually. One append operation briefly produced a malformed line:

```text
FOUNDRY_AGENT_NAME=hermes-foundry-gatewayTEAMS_APP_ID=...
```

It was corrected to:

```text
FOUNDRY_AGENT_NAME=hermes-foundry-gateway
TEAMS_APP_ID=74fc3705-eaa4-4a82-9585-4a4d1afa6d7a
```

## Commands that worked

### Publish Foundry Hosted Agent

```powershell
azd deploy --no-prompt -C C:\git\agent-demos\hermes-foundry
```

### Create A365 scaffold

```powershell
atk new `
  --capability foundry-agent-to-m365 `
  --app-name hermes-foundry-a365 `
  --foundry-endpoint https://tomaskubica-foundry-resource.services.ai.azure.com/api/projects/tomaskubica-foundry-project `
  --foundry-agent-id hermes-foundry-gateway `
  --folder C:\git\agent-demos `
  --interactive false
```

### Deploy bridge Azure resources manually

```powershell
az deployment group create `
  --subscription 673af34d-6b28-41dc-bc7b-f507418045e6 `
  --resource-group ai-services `
  --name hermes-a365-manual `
  --template-file C:\git\agent-demos\hermes-foundry-a365\infra\azure.bicep `
  --parameters `
    resourceBaseName=apphermesa365 `
    botDisplayName=hermes-foundry-a365 `
    foundryProjectEndpoint=https://tomaskubica-foundry-resource.services.ai.azure.com/api/projects/tomaskubica-foundry-project `
    foundryAgentName=hermes-foundry-gateway `
    webAppSku=B1
```

### Build and deploy bridge app

```powershell
cd C:\git\agent-demos\hermes-foundry-a365
npm install
npm run build
```

Then zip deployed `dist`, `node_modules`, `package.json`, and `web.config` to `apphermesa365`.

### Publish Teams app package

```powershell
m365 teams app publish `
  --filePath C:\git\agent-demos\hermes-foundry-a365\appPackage\build\appPackage.dev.zip
```

Result:

```json
{
  "id": "dcc85d42-a0c3-47d0-8328-757375273b85",
  "externalId": "74fc3705-eaa4-4a82-9585-4a4d1afa6d7a",
  "displayName": "hermes-foundry-a365dev",
  "distributionMethod": "organization"
}
```

### Install Teams app for user

```powershell
m365 teams app install `
  --id dcc85d42-a0c3-47d0-8328-757375273b85 `
  --userName tomas@tomasonline.net
```

## Validation evidence

### Foundry Hosted Agent

Validated over both Foundry protocols:

- `/responses` returned `hosted responses ok`
- `/invocations` returned `hosted invocation ok`
- Streaming `/responses` returned SSE and `[DONE]`

### Teams/A365 bridge

App Service logs confirmed Bot Framework traffic reached the bridge endpoint:

```text
POST /api/messages ... Microsoft-SkypeBotApi (Microsoft-BotFramework/3.0) ... 200
```

The app is installed for `tomas@tomasonline.net`:

```json
{
  "teamsAppId": "dcc85d42-a0c3-47d0-8328-757375273b85",
  "displayName": "hermes-foundry-a365dev",
  "publishingState": "published"
}
```

## Remaining validation

The next manual/CLI validation is conversational:

1. Open Teams as `tomas@tomasonline.net`.
2. Open `hermes-foundry-a365dev`.
3. Send a direct message.
4. Confirm App Service logs show a new `POST /api/messages`.
5. Confirm the bridge calls `hermes-foundry-gateway` and returns a response.

If message delivery reaches App Service but no response appears, inspect `src/agent.ts` and App Service logs for the Foundry Responses call path.

## Lessons learned

1. **Tenant/account clarity is critical.** Keep Azure, M365 publisher, and target test user explicit.
2. **Prefer device-code auth for multi-account work.** Browser/native-broker flows were ambiguous and brittle.
3. **Do not depend on Toolkit provision for this scenario yet.** The scaffold was useful, but manual CLI/API provisioning was more reliable.
4. **Teams app catalog publish requires both license and admin/app-catalog scopes.**
5. **Private/custom Teams apps can be blocked after successful publish.** Installation requires app permission policy compatibility.
6. **Foundry portal may require streaming-compatible Responses behavior.** CLI success does not guarantee Playground UX success.
7. **Managed identities need explicit data-plane RBAC.** `Cognitive Services OpenAI User` was required for model calls.
