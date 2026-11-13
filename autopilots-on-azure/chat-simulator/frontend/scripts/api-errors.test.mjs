import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { Api, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("committed agent delivery errors preserve diagnostics and explain retry without claiming rollback", async (t) => {
  const detail = { code: "agent_delivery_failed", resourceId: "fixture-id", resourceCommitted: true,
    failures: [{ code: "endpoint_failure", delivery: { status: "failed" } }] };
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({ detail }), { status: 502 }));
  const api = new Api(async () => "synthetic-fixture");
  await assert.rejects(api.request("/chats", "POST", {}), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 502);
    assert.match(error.message, /changes were saved/);
    assert.match(error.message, /retry/);
    assert.deepEqual(error.diagnostics, { detail });
    return true;
  });
});

test("ordinary API failures retain the server's human-readable detail", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({ detail: "Not a group participant." }),
    { status: 403 }));
  await assert.rejects(new Api(async () => "synthetic-fixture").request("/chats/example/members"),
    /Not a group participant/);
});
