# Step 06 observability portal investigation

Short version: App Insights telemetry works, but Foundry portal Conversations is not yet reliable for this Hosted Agent + Agent Framework + Responses path.

## Current state

- Foundry project is connected to App Insights resource `step06-foundry-observability-evals-ai`.
- Step 06 emits telemetry into App Insights.
- App Insights contains `gen_ai.*` spans with user input, assistant output, model calls, response IDs, token counts, and `_MS.GenAIContentId`.
- Foundry portal Sessions can show activity.
- Foundry portal Conversations can still be empty or incomplete.

This is not just "missing telemetry". The telemetry is there, but the portal conversation view appears to need specific conversation/thread indexing metadata.

## Evidence from App Insights

The useful spans are mostly in `dependencies` and `traces`:

- `invoke_agent FoundryObservabilityEvalsAgent`
- `chat gpt-5.4-mini`
- providers such as `microsoft.agent_framework`, `azure.ai.foundry`, and sometimes `microsoft.foundry`
- `gen_ai.input.messages`
- `gen_ai.output.messages`
- `gen_ai.response.id`
- `gen_ai.usage.input_tokens`
- `gen_ai.usage.output_tokens`

The hosted `requests` rows contain `gen_ai.conversation.id`, but the rich message/model spans do not. Those spans can share `operation_Id`, so App Insights can correlate them as one trace, but the Conversations blade appears to rely on conversation/thread attributes directly.

Useful query:

```kusto
union isfuzzy=true dependencies, traces, requests
| where timestamp > ago(24h)
| extend cd=customDimensions
| extend
    op=tostring(cd["gen_ai.operation.name"]),
    provider=tostring(cd["gen_ai.provider.name"]),
    conv=tostring(cd["gen_ai.conversation.id"]),
    thread=tostring(cd["gen_ai.thread.id"]),
    resp=tostring(cd["gen_ai.response.id"]),
    content=tostring(cd["_MS.GenAIContentId"])
| where op != "" or provider != "" or content != ""
| summarize
    count(),
    any_conv=any(conv),
    any_thread=any(thread),
    any_resp=any(resp),
    any_content=any(content),
    min_time=min(timestamp),
    max_time=max(timestamp)
  by itemType, name, op, provider
| order by max_time desc
```

## SDK issue found

Do not blindly enable:

```text
AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true
```

With the current preview package set, this can break streaming. The Azure Projects experimental Responses instrumentor wraps streaming Responses with an internal `AsyncStreamWrapper`. Agent Framework's OpenAI adapter uses `client.responses.with_raw_response.create(stream=True)` and expects the returned object to support `.parse()`. The wrapper does not always expose that method, so hosted streaming can fail with:

```text
AttributeError: 'AsyncStreamWrapper' object has no attribute 'parse'
```

Observed local package set:

- `agent-framework-core 1.9.0`
- `agent-framework-foundry 1.8.2`
- `agent-framework-foundry-hosting 1.0.0a260618`
- `agent-framework-openai 1.8.2`
- `azure-ai-agentserver-core 2.0.0b6`
- `azure-ai-agentserver-responses 1.0.0b7`
- `azure-ai-projects 2.2.0`
- `azure-monitor-opentelemetry 1.8.8`
- `openai 2.43.0`

## Safe current position

- Keep `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`.
- Keep App Insights project connection.
- Keep Azure Monitor export.
- Do not enable `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` in the hosted Step 06 agent until streaming compatibility is re-tested.

## Possible workaround to test later

Try disabling only the experimental Responses API wrapper:

```text
AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true
AZURE_TRACING_GEN_AI_INSTRUMENT_RESPONSES_API=false
```

This might allow other GenAI/agent instrumentation while avoiding the Responses streaming wrapper that can break `.parse()`. Treat this as an experiment, not a committed default, until hosted Responses and AG-UI smoke tests pass.

## Desired future state

The target is:

- Foundry Hosted Agent remains the agent runtime.
- BFF/ACA handles AG-UI and client integration.
- Foundry can stay on Responses/Hosted Agent protocols.
- App Insights has complete traces.
- Foundry portal Traces and Conversations show useful conversation history without a separate fake seeder path.

## What future agents should check

Before changing Step 06 again, check these items:

1. Review current Microsoft docs for Foundry agent tracing, especially whether Hosted Agents and Microsoft Agent Framework are still preview or now GA.
2. Check `azure-ai-projects` release notes after `2.2.0` for fixes to `_ResponsesInstrumentorPreview`, streaming Responses, `AsyncStreamWrapper`, raw responses, or `.parse()`.
3. Check `agent-framework-openai` and `agent-framework-foundry` release notes for changes from `with_raw_response.create(stream=True).parse()` to `responses.stream(...)`, or any compatibility note for Azure Projects GenAI tracing.
4. Re-test hosted Step 06 with:
   - no experimental flag,
   - `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`,
   - and `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` plus `AZURE_TRACING_GEN_AI_INSTRUMENT_RESPONSES_API=false`.
5. After each deploy, run all relevant traffic:
   - local or hosted `/responses`,
   - AG-UI/BFF path if present,
   - streaming invocation path,
   - several multi-turn conversations with stable conversation IDs.
6. Query App Insights and verify message-rich spans include either:
   - `gen_ai.conversation.id`, or
   - `gen_ai.thread.id` and `gen_ai.thread.run.id`, or
   - whatever newer Foundry docs say the Conversations blade requires.
7. Open Foundry portal Traces and Conversations. Do not call this done unless the portal shows the conversations, not only raw App Insights rows.
8. If portal Conversations still requires the Azure AI Agents thread/run model, keep the limitation documented and consider a separate official trace seeder only as a teaching aid.

## Bug report to file or track

File or track an issue in `Azure/azure-sdk-for-python`:

```text
azure-ai-projects experimental Responses instrumentation wraps async streaming responses with AsyncStreamWrapper that does not preserve the with_raw_response.create(stream=True).parse() contract used by Agent Framework.
```

Include:

- `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`
- `azure-ai-projects 2.2.0`
- `openai 2.43.0`
- `agent-framework-openai 1.8.2`
- `agent-framework-foundry 1.8.2`
- error: `AttributeError: 'AsyncStreamWrapper' object has no attribute 'parse'`

## Do not forget

Step 05 architecture changed. AG-UI should live in the ACA BFF. Step 06 and later steps should not assume direct AG-UI exposure from Foundry Hosted Agent. The Hosted Agent can stay on Responses/Invocations while the BFF adapts to AG-UI.
