import readline from "node:readline";
import { Type } from "typebox";
import {
  createAgentSession, createExtensionRuntime, ModelRuntime,
  SessionManager, SettingsManager,
} from "@earendil-works/pi-coding-agent";

const lines = readline.createInterface({ input: process.stdin });
const waiting = new Map();
let initialized;
const first = new Promise(resolve => { initialized = resolve; });
let initial = true;
lines.on("line", line => {
  const message = JSON.parse(line);
  if (initial) {
    initial = false;
    initialized(message);
  } else {
    const resolve = waiting.get(message.id);
    if (!resolve) throw new Error("Unknown host response ID.");
    waiting.delete(message.id);
    resolve(message.result);
  }
});
const emit = value => process.stdout.write(JSON.stringify(value) + "\n");
const config = await first;
const runtime = await ModelRuntime.create({
  authPath: `${config.directory}\\pi-auth-unused.json`,
  modelsPath: null, refreshOnCreate: false, allowModelNetwork: false,
});
runtime.registerProvider("foundry", {
  baseUrl: config.model.base_url, api: "openai-responses", apiKey: config.token,
  authHeader: true,
  models: [{
    id: config.model.deployment, name: "Foundry Sol",
    api: "openai-responses", reasoning: true, input: ["text"],
    contextWindow: 64000, maxTokens: config.model.max_output_tokens,
    cost: { input: 2, output: 10, cacheRead: 0.1, cacheWrite: 2.5 },
  }],
});
const loader = {
  getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
  getSkills: () => ({ skills: [], diagnostics: [] }),
  getPrompts: () => ({ prompts: [], diagnostics: [] }),
  getThemes: () => ({ themes: [], diagnostics: [] }),
  getAgentsFiles: () => ({ agentsFiles: [] }),
  getSystemPrompt: () => config.instructions,
  getSystemPromptSource: () => undefined,
  getAppendSystemPrompt: () => [],
  getAppendSystemPromptSources: () => [],
  extendResources: () => {},
  reload: async () => {},
};
let nextId = 0;
const customTools = config.tools.map(spec => ({
  name: spec.name, label: spec.name, description: spec.description,
  parameters: Type.Object(Object.fromEntries(
    Object.keys(spec.parameters.properties).map(key => [key, Type.String()]),
  )),
  execute: async (_callId, args) => {
    const id = ++nextId;
    const result = new Promise(resolve => waiting.set(id, resolve));
    emit({ type: "tool", id, name: spec.name, arguments: args });
    return { content: [{ type: "text", text: JSON.stringify(await result) }], details: {} };
  },
}));
let session;
try {
  ({ session } = await createAgentSession({
    cwd: config.directory, agentDir: config.directory,
    modelRuntime: runtime, model: runtime.getModel("foundry", config.model.deployment),
    thinkingLevel: config.model.reasoning_effort,
    tools: config.tools.map(spec => spec.name), customTools,
    resourceLoader: loader,
    sessionManager: SessionManager.inMemory(config.directory),
    settingsManager: SettingsManager.inMemory({
      retry: { enabled: false },
      compaction: { enabled: true, reserveTokens: 6000, keepRecentTokens: 16000 },
    }),
  }));
  let calls = 0;
  session.subscribe(event => {
    if (event.type === "message_end" && event.message.role === "assistant") {
      emit({ type: "usage", usage: event.message.usage });
      if (++calls >= config.max_model_calls) session.abort();
    }
  });
  emit({ type: "ready" });
  await session.prompt(config.prompt);
  emit({ type: "done", text: session.getLastAssistantText() ?? "" });
} catch (error) {
  emit({ type: "error", error: String(error) });
  process.exitCode = 1;
} finally {
  session?.dispose();
  lines.close();
}
