# Step 06: Foundry observability and evals

Goal: traces, logs, correlation IDs, generated conversations, evals.

Flow:

```text
AG-UI client -> Foundry /invocations -> instrumented agent -> OpenTelemetry -> App Insights / Foundry tracing
Eval runner -> Foundry /invocations -> eval report
```

Personality:

```text
professional, calm, friendly, concise
not cold, hype-driven, sarcastic, theatrical, or overly excited
```

Run local:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
# Optional: $env:APPLICATIONINSIGHTS_CONNECTION_STRING = "<real connection string>"
uv sync
uv run python main.py
```

Smoke local:

```powershell
uv run python smoke_agui.py --url http://127.0.0.1:8088/invocations
```

Deploy:

```powershell
azd deploy -C progressive-agents\06-foundry-observability-evals
```

Smoke deployed:

```powershell
uv run python smoke_agui.py
```

Watch hosted logs:

```powershell
azd ai agent invoke step-06-foundry-observability-evals '{"message":"Say one calm sentence about traces.","stream":false}' --protocol invocations
azd ai agent monitor step-06-foundry-observability-evals --tail 80
```

Generate example conversations:

```powershell
uv run python evals\generate_conversations.py
```

Run personality eval:

```powershell
uv run python evals\run_evals.py --generated evals\generated_conversations.jsonl
```

Try Foundry-generated eval suite:

```powershell
azd ai agent eval generate --agent step-06-foundry-observability-evals --gen-instruction-file evals\agent_eval_instruction.md --eval-model gpt-5.4-mini --max-samples 15 --out-file eval.yaml
azd ai agent eval run --config eval.yaml
```

Foundry-generated suite included:

```text
eval.yaml
datasets\smoke-core
evaluators\smoke-core\rubric_dimensions.json
```

Rubric:

```text
evals\rubric_personality_grader.json
```

Observability status:

```text
Hosted logs work.
JSON correlation log visible in azd monitor.
Custom spans exist in code.
Foundry project connected to App Insights: step06-foundry-observability-evals-ai.
Hosted startup shows appinsights_configured=True.
Azure Monitor query returns emitted traces.
```

Done means:

- AG-UI still streams.
- SSE events include `correlationId`.
- Logs are JSON.
- Custom spans exist: request, model, tool, memory, skill, worker.
- Eval report passes.
