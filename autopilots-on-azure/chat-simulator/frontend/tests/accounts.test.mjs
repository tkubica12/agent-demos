import assert from "node:assert/strict";
import test from "node:test";
import { selectCachedAccount } from "../src/auth/accounts.ts";

const config = { tenantId: "demo-tenant", clientId: "simulator-client" };
function account(id, roles = ["User"], tenantId = config.tenantId, aud = config.clientId) {
  return {
    homeAccountId: id, localAccountId: id, environment: "login.microsoftonline.com",
    username: `${id}@example.invalid`, tenantId, idTokenClaims: { aud, roles },
  };
}

test("a stale unassigned SSO identity cannot displace the only assigned account", () => {
  const stale = account("other-sso", []);
  const requested = account("requested");
  assert.equal(selectCachedAccount([stale, requested], stale, config), requested);
});

test("the active assigned account wins when several identities are cached", () => {
  const user = account("user");
  const admin = account("admin", ["User", "Admin"]);
  assert.equal(selectCachedAccount([user, admin], admin, config), admin);
});

test("multiple assigned identities without an active choice require account selection", () => {
  assert.equal(selectCachedAccount([account("one"), account("two")], null, config), undefined);
});

test("foreign tenants, other clients and unknown roles cannot become the cached identity", () => {
  const rejected = [
    account("wrong-tenant", ["User"], "another-tenant"),
    account("wrong-client", ["User"], config.tenantId, "another-client"),
    account("unknown-role", ["Reader"]),
    account("unassigned", []),
  ];
  for (const active of rejected) {
    assert.equal(selectCachedAccount(rejected, active, config), undefined);
  }
});

test("an empty cache requires a fresh sign-in", () => {
  assert.equal(selectCachedAccount([], null, config), undefined);
});
