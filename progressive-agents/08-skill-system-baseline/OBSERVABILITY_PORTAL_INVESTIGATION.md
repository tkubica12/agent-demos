# Step 08 observability evidence

Step 08 exports Hosted Agent, Agent Framework, model, skill-tool, memory, and
custom correlation spans to the Application Insights resource connected to the
Foundry project.

## Verified deployment

Validated on July 16, 2026 with hosted agent version 8 and protocol version
2.0.0 for Responses and Invocations.

The deployed escalation smoke produced one correlated operation containing:

```text
operation_Id: bfa6fb5dd096ebe01d1490fba6287d1d
started: 2026-07-16T13:38:37Z
```

```text
request.received
memory.read
model.call
invoke_agent SkillSystemBaselineAgent
chat gpt-5.4-mini
execute_tool load_skill
execute_tool read_skill_resource
memory.write
```

Both skill tool spans completed successfully. This proves that the deployed
agent loaded the escalation skill and read its checked-in escalation matrix;
it is not a synthetic zero-count span.

The deployed memory smoke produced correlated `memory.read`, `model.call`,
`invoke_agent`, `chat`, and `memory.write` spans and returned the marker stored
through Foundry Memory in a separate hosted session.

## Query

Use the operation ID from an `invoke_agent SkillSystemBaselineAgent` row to
inspect a complete trace:

```kusto
dependencies
| where timestamp > ago(24h)
| where name == "invoke_agent SkillSystemBaselineAgent"
| project timestamp, operation_Id, duration, success
| order by timestamp desc
```

Then inspect one operation:

```kusto
dependencies
| where operation_Id == "<operation-id>"
| project timestamp, name, type, duration, success, customDimensions
| order by timestamp asc
```

## Current limitations

- Foundry Memory and Agent Skills remain preview capabilities.
- The Foundry Conversations view can lag or omit Agent Framework conversation
  indexing even when the correlated App Insights spans are present.
- Do not enable `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` by default. The
  Azure Projects preview Responses wrapper can still interfere with streaming;
  the current Agent Framework and Azure Monitor instrumentation already emits
  the verified spans above.
- The Microsoft OpenTelemetry distro may log an optional Agent 365
  instrumentation warning when the unrelated `openai-agents` package is not
  installed. Agent Framework and Azure Monitor traces continue to export.

## Validated package set

```text
agent-framework-core 1.11.0
agent-framework-foundry 1.10.1
agent-framework-foundry-hosting 1.0.0a260709
azure-ai-agentserver-core 2.0.0b7
azure-ai-agentserver-responses 1.0.0b8
azure-ai-projects 2.3.0
azure-monitor-opentelemetry 1.8.9
openai 2.45.0
```
