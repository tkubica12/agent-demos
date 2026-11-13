"""Reconcile standard, secretless Entra identities for the simulator.

Run with ``uv run python -m scripts.simulator_identity --config CONFIG.toml
--bindings BINDINGS.json --state STATE.json [--redirect-uri URI] [--destroy]``.
All configured principals must be object IDs, not user/group display names.
State contains owned identity IDs and ownership context only. A Graph 403 writes
STATE.admin-request.json with inputs and conditional, reference-linked Graph calls;
it never converts a failed reconciliation into success.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
import tomllib
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from azure.identity import AzureCliCredential


GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_EXCHANGE = "api://AzureADTokenExchange"
TAG = "autopilots-simulator"
APP_SELECT = "id,appId,displayName,tags,signInAudience,api,appRoles,spa,identifierUris,requiredResourceAccess"
SP_SELECT = "id,appId,tags,appOwnerOrganizationId,appRoleAssignmentRequired"
READINESS_DELAYS = (1, 2, 4, 8, 16)
PERMISSION_PUBLICATION_MESSAGE = (
    "Property api.preAuthorizedApplications.delegatedPermissionIds has a Permission Id "
    "that cannot be found in the AppPermissions sets."
)


class GraphPermissionsError(PermissionError):
    """Graph denied an operation; an administrator request has been saved."""


def _uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{label} must be an object ID (UUID); named principals are not resolved.") from exc


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError(f"{label} must be 1-64 letters, digits, dots, underscores or hyphens.")
    return value


def _context(config: dict) -> dict:
    context = {"environment": _name(config.get("environment"), "environment")}
    for key in ("tenant_id", "subscription_id"):
        context[key] = _uuid(config.get(key), key)
    return context


def _assignments(config: dict) -> list[dict]:
    return config.get("assignments", [
        {"principal_id": config["owner_id"], "principal_type": "User", "role": "Admin"},
    ])


def _validate(config: dict, bindings: dict | None = None, redirect_uri: str = "") -> dict:
    context = _context(config)
    _uuid(config.get("owner_id"), "owner_id")
    names = set()
    for agent in config.get("agents", []):
        name = _name(agent.get("name"), "agent name")
        if name.casefold() in names:
            raise ValueError(f"Duplicate agent name: {name}")
        names.add(name.casefold())
        display = agent.get("display_name")
        if not isinstance(display, str) or not display.strip() or len(display) > 128:
            raise ValueError(f"Agent {name} requires a display_name of 1-128 characters.")
    if not isinstance(_assignments(config), list):
        raise ValueError("assignments must be a list; omit it to assign the owner Admin or use [] to deny everyone.")
    for assignment in _assignments(config):
        _uuid(assignment.get("principal_id"), "assignment principal_id")
        if assignment.get("principal_type") not in {"User", "Group"}:
            raise ValueError("assignment principal_type must be User or Group.")
        if assignment.get("role") not in {"User", "Admin"}:
            raise ValueError("assignment role must be User or Admin.")
    displays = [display.casefold() for _, display in _specs(config)]
    if len(set(displays)) != len(displays):
        raise ValueError("Simulator, channel and worker display names must be distinct.")
    if redirect_uri:
        uri = urlsplit(redirect_uri)
        if (not uri.hostname or uri.fragment or uri.username or uri.password
                or not (uri.scheme == "https"
                        or (uri.scheme == "http" and uri.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise ValueError("SPA redirect_uri must use HTTPS (HTTP is allowed for localhost), without a fragment.")
    if bindings is not None:
        for kind, _ in _specs(config):
            if kind == "spa":
                continue
            binding = _binding(bindings, kind)
            for key in ("identity_client_id", "identity_principal_id"):
                _uuid(binding.get(key), f"{kind} {key}")
            runtime = binding.get("runtime_identity_principal_id")
            if runtime:
                _uuid(runtime, f"{kind} runtime_identity_principal_id")
                if _uuid(runtime, "runtime identity") == _uuid(binding["identity_principal_id"], "gateway identity"):
                    raise ValueError(f"{kind} gateway and runtime identities must be distinct.")
    return context


def _specs(config: dict) -> list[tuple[str, str]]:
    prefix = f"Autopilots Simulator {config['environment']}"
    return [("spa", prefix), ("channel", f"{prefix} Channel")] + [
        (f"agents.{agent['name']}", f"{prefix} {agent['display_name']}") for agent in config.get("agents", [])
    ]


def _binding(bindings: dict, kind: str) -> dict:
    if kind == "channel":
        return bindings.get("channel", {})
    return bindings.get("agents", {}).get(kind.removeprefix("agents."), {})


def _namespace(context: dict) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, "/".join([TAG, context["tenant_id"],
                                                 context["subscription_id"], context["environment"]]))


def _tags(context: dict, kind: str) -> list[str]:
    return [TAG, f"{TAG}:environment={context['environment']}",
            f"{TAG}:context={_namespace(context)}", f"{TAG}:identity={kind}"]


def _definition(context: dict, kind: str, display: str, client_id: str, redirect_uri: str,
                current: dict | None = None) -> dict:
    body = {"displayName": display, "signInAudience": "AzureADMyOrg", "tags": _tags(context, kind)}
    if kind != "spa":
        return body
    scope_id = str(uuid.uuid5(_namespace(context), "access_as_user"))
    body.update({
        "identifierUris": [f"api://{client_id}"],
        "api": {
            "requestedAccessTokenVersion": 2,
            "oauth2PermissionScopes": [{
                "id": scope_id, "value": "access_as_user", "isEnabled": True, "type": "User",
                "adminConsentDisplayName": "Access the simulator",
                "adminConsentDescription": "Access the simulator as the signed-in user.",
                "userConsentDisplayName": "Access the simulator",
                "userConsentDescription": "Access the simulator on your behalf.",
            }],
        },
        "appRoles": [{
            "id": str(uuid.uuid5(_namespace(context), f"role:{role}")),
            "value": role, "displayName": role, "description": f"Simulator {role.lower()} access.",
            "allowedMemberTypes": ["User"], "isEnabled": True,
        } for role in ("User", "Admin")],
        "spa": {"redirectUris": [redirect_uri] if redirect_uri else
                ((current or {}).get("spa") or {}).get("redirectUris", [])},
        "requiredResourceAccess": [{"resourceAppId": client_id,
                                    "resourceAccess": [{"id": scope_id, "type": "Scope"}]}],
    })
    return body


def _federation(context: dict, kind: str, binding: dict) -> list[dict]:
    label = kind.removeprefix("agents.")
    principals = {"gateway": binding["identity_principal_id"]}
    if binding.get("runtime_identity_principal_id"):
        principals["runtime"] = binding["runtime_identity_principal_id"]
    return [{
        "name": f"simulator-{label}-{part}",
        "issuer": f"https://login.microsoftonline.com/{context['tenant_id']}/v2.0",
        "subject": _uuid(principal, f"{part} principal"),
        "audiences": [TOKEN_EXCHANGE],
    } for part, principal in principals.items()]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.writing")
    try:
        staging.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _load(path: Path, context: dict) -> dict:
    if not path.exists():
        return {**context, "agents": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if any(state.get(key) != value for key, value in context.items()):
        raise ValueError("Identity state belongs to another environment, tenant or subscription.")
    for kind, record in _records(state):
        if kind.startswith("agents."):
            _name(kind.removeprefix("agents."), "state agent name")
        for key in ("object_id", "client_id"):
            _uuid(record.get(key), f"state {kind} {key}")
        if record.get("service_principal_id"):
            _uuid(record["service_principal_id"], f"state {kind} service_principal_id")
    return state


def _records(state: dict) -> list[tuple[str, dict]]:
    return [(kind, state[kind]) for kind in ("spa", "channel") if state.get(kind)] + [
        (f"agents.{name}", record) for name, record in state.get("agents", {}).items()
    ]


def _slot(state: dict, kind: str) -> tuple[dict, str]:
    return (state.setdefault("agents", {}), kind.removeprefix("agents.")) if kind.startswith("agents.") else (state, kind)


def _filter(collection: str, expression: str, select: str) -> str:
    count = "&$count=true" if "tags/any" in expression else ""
    return f"/{collection}?$filter={quote(expression, safe='')}&$select={select}{count}"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _matches(current: object, desired: object) -> bool:
    if isinstance(desired, dict):
        return isinstance(current, dict) and all(_matches(current.get(key), value) for key, value in desired.items())
    if isinstance(desired, list):
        return (isinstance(current, list) and len(current) == len(desired)
                and all(any(_matches(item, expected) for item in current) for expected in desired))
    return current == desired


class GraphClient:
    """Small v1.0 Graph client using only the selected Azure CLI identity."""

    def __init__(self, tenant_id: str, subscription_id: str):
        self.credential = AzureCliCredential(tenant_id=tenant_id)
        self.client = httpx.Client(timeout=60, headers={"ConsistencyLevel": "eventual"})

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()
        self.credential.close()

    def request(self, method: str, path: str, *, body: dict | None = None) -> dict:
        url = path if path.startswith("https://") else GRAPH + path
        if not url.startswith(GRAPH + "/"):
            raise ValueError("Refusing to send a Graph token to an unexpected pagination URL.")
        token = self.credential.get_token(TOKEN_SCOPE)
        response = self.client.request(method, url, json=body,
                                       headers={"Authorization": f"Bearer {token.token}"})
        response.raise_for_status()
        return response.json() if response.content else {}


class _Reconciler:
    def __init__(self, graph: GraphClient, config: dict, bindings: dict, path: Path, state: dict):
        self.graph, self.config, self.bindings, self.path, self.state = graph, config, bindings, path, state
        self.context = _context(config)
        self.calls: list[dict] = []
        self.pending_creations = {
            ("applications", record["object_id"]) for _, record in _records(state)
            if not record.get("service_principal_id")
        }

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append({"method": method, "path": path, **({"body": copy.deepcopy(body)} if body is not None else {})})
        return self.graph.request(method, path, body=body)

    def dependent_call(self, parent: tuple[str, str], method: str, path: str,
                       body: dict | None = None, *, consent_scope: tuple[str, str] | None = None) -> dict:
        """Bound resource-readiness and intended self-scope publication retries."""
        pending_creation = parent in self.pending_creations
        permission_publication = (
            consent_scope is not None and parent[0] == "applications" and method == "PATCH"
            and path == f"/applications/{parent[1]}"
            and any(item.get("appId") == consent_scope[0]
                    and consent_scope[1] in item.get("delegatedPermissionIds", [])
                    for item in (body or {}).get("api", {}).get("preAuthorizedApplications", []))
        )
        delays = iter(READINESS_DELAYS if pending_creation or permission_publication else ())
        while True:
            try:
                return self.call(method, path, body)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404 and pending_creation:
                    pass
                elif exc.response.status_code == 400 and permission_publication:
                    try:
                        error = exc.response.json().get("error")
                    except (ValueError, AttributeError):
                        raise exc
                    if (not isinstance(error, dict) or error.get("code") != "InvalidValue"
                            or error.get("message") != PERMISSION_PUBLICATION_MESSAGE):
                        raise
                else:
                    raise
                delay = next(delays, None)
                if delay is None:
                    raise
                time.sleep(delay)

    def optional(self, path: str) -> dict | None:
        try:
            return self.call("GET", path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def values(self, path: str, *, parent: tuple[str, str] | None = None) -> list[dict]:
        values = []
        while path:
            payload = self.dependent_call(parent, "GET", path) if parent else self.call("GET", path)
            values.extend(payload.get("value", []))
            path = payload.get("@odata.nextLink", "")
        return values

    def save(self) -> None:
        _write(self.path, self.state)

    def validate_tenant(self) -> None:
        organizations = self.values("/organization?$select=id")
        if len(organizations) != 1 or organizations[0].get("id", "").lower() != self.context["tenant_id"]:
            raise ValueError("Azure CLI Graph tenant does not match config tenant_id.")

    def owned(self, resource: dict, kind: str, *, client_id: str = "", principal: bool = False) -> None:
        if not set(_tags(self.context, kind)).issubset(resource.get("tags") or []):
            raise ValueError(f"Refusing foreign ownership for {kind}: {resource.get('id')}.")
        if client_id and resource.get("appId") != client_id:
            raise ValueError(f"Refusing mismatched client_id for {kind}: {resource.get('id')}.")
        if principal:
            tenant = resource.get("appOwnerOrganizationId")
            if tenant and tenant.lower() != self.context["tenant_id"]:
                raise ValueError(f"Refusing foreign tenant service principal for {kind}.")
        elif resource.get("signInAudience") != "AzureADMyOrg":
            raise ValueError(f"Refusing non-single-tenant application for {kind}.")

    def discover(self, kind: str, display: str, owned_apps: list[dict]) -> dict | None:
        matches = self.values(_filter("applications", f"displayName eq {_literal(display)}", APP_SELECT))
        matches += [app for app in owned_apps if _tags(self.context, kind)[-1] in (app.get("tags") or [])]
        parent, key = _slot(self.state, kind)
        record = parent.get(key, {})
        if record:
            resource = ("applications", record["object_id"])
            path = f"/applications/{record['object_id']}?$select={APP_SELECT}"
            app = (self.dependent_call(resource, "GET", path) if resource in self.pending_creations
                   else self.optional(path))
            if app:
                self.owned(app, kind, client_id=record["client_id"])
                matches.append(app)
        unique = {app["id"]: app for app in matches}
        for app in unique.values():
            self.owned(app, kind)
        if len(unique) > 1:
            raise ValueError(f"Ambiguous application matches for {kind}; no identities were adopted.")
        return next(iter(unique.values()), None)

    def ensure_owner(self, collection: str, object_id: str) -> None:
        path = f"/{collection}/{object_id}/owners"
        resource = (collection, object_id)
        owner = _uuid(self.config["owner_id"], "owner_id")
        if not any(item["id"] == owner for item in self.values(path + "?$select=id", parent=resource)):
            self.dependent_call(resource, "POST", path + "/$ref", {"@odata.id": f"{GRAPH}/directoryObjects/{owner}"})

    def ensure_app(self, kind: str, display: str, app: dict | None, redirect_uri: str) -> dict:
        created = app is None
        if app is None:
            body = _definition(self.context, kind, display, "", "")
            body = {key: body[key] for key in ("displayName", "signInAudience", "tags")}
            app = self.call("POST", "/applications", body)
        parent, key = _slot(self.state, kind)
        previous = parent.get(key, {})
        record = {"object_id": _uuid(app["id"], "application id"), "client_id": _uuid(app["appId"], "application appId")}
        if previous.get("object_id") == record["object_id"] and previous.get("service_principal_id"):
            record["service_principal_id"] = previous["service_principal_id"]
        parent[key] = record
        self.save()
        resource = ("applications", record["object_id"])
        if created:
            self.pending_creations.add(resource)
        desired = _definition(self.context, kind, display, record["client_id"], redirect_uri, app)
        self_access = desired.pop("requiredResourceAccess", None)
        changed = {key: value for key, value in desired.items() if not _matches(app.get(key), value)}
        if changed:
            self.dependent_call(resource, "PATCH", f"/applications/{record['object_id']}", changed)
        if kind == "spa":
            scope_id = str(uuid.uuid5(_namespace(self.context), "access_as_user"))
            authorized = copy.deepcopy((app.get("api") or {}).get("preAuthorizedApplications") or [])
            own_client = next((item for item in authorized if item["appId"] == record["client_id"]), None)
            if own_client is None:
                authorized.append({"appId": record["client_id"], "delegatedPermissionIds": [scope_id]})
            elif scope_id not in own_client.get("delegatedPermissionIds", []):
                own_client.setdefault("delegatedPermissionIds", []).append(scope_id)
            delegated = {"requiredResourceAccess": self_access,
                         "api": {"preAuthorizedApplications": authorized}}
            consent_changes = {key: value for key, value in delegated.items() if not _matches(app.get(key), value)}
            if consent_changes:
                self.dependent_call(resource, "PATCH", f"/applications/{record['object_id']}", consent_changes,
                                    consent_scope=(record["client_id"], scope_id))
        self.ensure_owner("applications", record["object_id"])
        principals = self.values(_filter("servicePrincipals", f"appId eq '{record['client_id']}'", SP_SELECT))
        if len(principals) > 1:
            raise ValueError(f"Ambiguous service principals for {kind}.")
        if principals:
            principal = principals[0]
            self.owned(principal, kind, client_id=record["client_id"], principal=True)
        else:
            principal = self.dependent_call(resource, "POST", "/servicePrincipals", {
                "appId": record["client_id"], "tags": _tags(self.context, kind),
                "appRoleAssignmentRequired": kind == "spa",
            })
        record["service_principal_id"] = _uuid(principal["id"], "service principal id")
        self.save()
        principal_resource = ("servicePrincipals", record["service_principal_id"])
        if not principals:
            self.pending_creations.add(principal_resource)
        if principal.get("appRoleAssignmentRequired") != (kind == "spa"):
            self.dependent_call(principal_resource, "PATCH", f"/servicePrincipals/{principal['id']}",
                                {"appRoleAssignmentRequired": kind == "spa"})
        self.ensure_owner("servicePrincipals", principal["id"])
        return record

    def validate_bindings(self) -> None:
        for kind, _ in _specs(self.config):
            if kind == "spa":
                continue
            binding = _binding(self.bindings, kind)
            for part in ("identity_principal_id", "runtime_identity_principal_id"):
                if not binding.get(part):
                    continue
                principal_id = _uuid(binding[part], part)
                principal = self.call("GET", f"/servicePrincipals/{principal_id}?$select=id,appId,servicePrincipalType")
                if principal.get("servicePrincipalType") != "ManagedIdentity":
                    raise ValueError(f"{kind} {part} is not a managed identity.")
                if part == "identity_principal_id" and principal.get("appId") != _uuid(binding["identity_client_id"], "identity_client_id"):
                    raise ValueError(f"{kind} identity_client_id does not match identity_principal_id.")

    def ensure_federation(self, kind: str, record: dict) -> None:
        path = f"/applications/{record['object_id']}/federatedIdentityCredentials"
        resource = ("applications", record["object_id"])
        desired = _federation(self.context, kind, _binding(self.bindings, kind))
        current = self.values(path, parent=resource)
        canonical = {f"simulator-{kind.removeprefix('agents.')}-{part}" for part in ("gateway", "runtime")}
        for item in current:
            target = next((fic for fic in desired if fic["name"] == item["name"]), None)
            if item["name"] in canonical and (target is None or any(item.get(k) != v for k, v in target.items())):
                self.call("DELETE", f"{path}/{item['id']}")
            elif target is None:
                if any(item.get("issuer") == fic["issuer"] and item.get("subject") == fic["subject"] for fic in desired):
                    raise ValueError(f"Conflicting noncanonical federated credential for {kind}: {item['name']}")
        for fic in desired:
            if not any(all(item.get(k) == v for k, v in fic.items()) for item in current):
                self.dependent_call(resource, "POST", path, fic)

    def ensure_assignments(self, record: dict) -> None:
        path = f"/servicePrincipals/{record['service_principal_id']}/appRoleAssignedTo"
        resource = ("servicePrincipals", record["service_principal_id"])
        roles = {role: str(uuid.uuid5(_namespace(self.context), f"role:{role}")) for role in ("User", "Admin")}
        desired = set()
        for assignment in _assignments(self.config):
            principal = _uuid(assignment["principal_id"], "assignment principal_id")
            collection = "users" if assignment["principal_type"] == "User" else "groups"
            self.call("GET", f"/{collection}/{principal}?$select=id")
            desired.add((principal, roles[assignment["role"]]))
        current = self.values(path, parent=resource)
        for item in current:
            if (item.get("appRoleId") in roles.values()
                    and (item["principalId"], item["appRoleId"]) not in desired):
                self.call("DELETE", f"{path}/{item['id']}")
        for principal, role in sorted(desired):
            if not any(item["principalId"] == principal and item["appRoleId"] == role for item in current):
                self.dependent_call(resource, "POST", path, {
                    "principalId": principal, "resourceId": record["service_principal_id"], "appRoleId": role,
                })

    def reconcile(self, redirect_uri: str) -> dict:
        self.validate_tenant()
        self.validate_bindings()
        context_tag = _tags(self.context, "spa")[2]
        apps = self.values(_filter("applications", f"tags/any(t:t eq '{context_tag}')", APP_SELECT))
        discovered = {kind: self.discover(kind, display, apps) for kind, display in _specs(self.config)}
        result: dict = {"agents": {}}
        for kind, display in _specs(self.config):
            record = self.ensure_app(kind, display, discovered[kind], redirect_uri)
            if kind == "spa":
                self.ensure_assignments(record)
            else:
                self.ensure_federation(kind, record)
            parent, key = _slot(result, kind)
            parent[key] = dict(record)
        return result

    def destroy(self) -> None:
        self.validate_tenant()
        verified = []
        for kind, record in _records(self.state):
            app = self.optional(f"/applications/{record['object_id']}?$select={APP_SELECT}")
            if app:
                self.owned(app, kind, client_id=record["client_id"])
            principals = self.values(_filter("servicePrincipals", f"appId eq '{record['client_id']}'", SP_SELECT))
            if record.get("service_principal_id"):
                principal = self.optional(f"/servicePrincipals/{record['service_principal_id']}?$select={SP_SELECT}")
                if principal and all(item["id"] != principal["id"] for item in principals):
                    principals.append(principal)
            for principal in principals:
                self.owned(principal, kind, client_id=record["client_id"], principal=True)
            verified.append((kind, record, app, principals))
        for kind, record, app, principals in verified:
            for principal in principals:
                self.delete(f"/servicePrincipals/{principal['id']}")
            record.pop("service_principal_id", None)
            self.save()
            if app:
                self.delete(f"/applications/{record['object_id']}")
            parent, key = _slot(self.state, kind)
            del parent[key]
            self.save()
        self.path.unlink(missing_ok=True)

    def delete(self, path: str) -> None:
        try:
            self.call("DELETE", path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise


def _proposed_calls(config: dict, bindings: dict, state: dict, redirect_uri: str, destroy: bool) -> list[dict]:
    """Describe conditional requests, with references to IDs Graph has not allocated."""
    context = _context(config)
    calls: list[dict] = []

    def add(method: str, path: str, body: dict | None = None, when: str = "always", **metadata: str) -> None:
        calls.append({"method": method, "path": path, "when": when, **metadata,
                      **({"body": body} if body is not None else {})})

    add("GET", "/organization?$select=id")
    if destroy:
        for kind, record in _records(state):
            add("GET", f"/applications/{record['object_id']}?$select={APP_SELECT}")
            add("GET", _filter("servicePrincipals", f"appId eq '{record['client_id']}'", SP_SELECT))
            if record.get("service_principal_id"):
                add("GET", f"/servicePrincipals/{record['service_principal_id']}?$select={SP_SELECT}")
            add("DELETE", "/servicePrincipals/${" + kind + ".service_principal_id}",
                when="present, after all resources pass ownership checks")
            add("DELETE", f"/applications/{record['object_id']}", when="present and ownership verified")
        return calls
    add("GET", _filter("applications", f"tags/any(t:t eq '{_tags(context, 'spa')[2]}')", APP_SELECT))
    for kind, display in _specs(config):
        parent, key = _slot(state, kind)
        record = parent.get(key, {})
        object_id = record.get("object_id", "${" + kind + ".object_id}")
        client_id = record.get("client_id", "${" + kind + ".client_id}")
        principal_id = record.get("service_principal_id", "${" + kind + ".service_principal_id}")
        add("GET", _filter("applications", f"displayName eq {_literal(display)}", APP_SELECT))
        add("GET", f"/applications/{object_id}?$select={APP_SELECT}", when="recorded in state")
        definition = _definition(context, kind, display, client_id, redirect_uri)
        self_access = definition.pop("requiredResourceAccess", None)
        if kind == "spa" and not redirect_uri:
            definition.pop("spa")
        add("POST", "/applications", {k: definition[k] for k in ("displayName", "signInAudience", "tags")},
            when="no matching owned application; reject foreign or ambiguous matches", result=kind)
        add("PATCH", f"/applications/{object_id}", definition, when="owned application differs")
        if kind == "spa":
            add("PATCH", f"/applications/{object_id}", {
                "requiredResourceAccess": self_access,
                "api": {"preAuthorizedApplications": [{
                    "appId": client_id,
                    "delegatedPermissionIds": [str(uuid.uuid5(_namespace(context), "access_as_user"))],
                }]},
            }, when="self delegated access or preauthorization missing; scope must already be persisted; "
                    "preserve other authorized clients")
        add("GET", _filter("servicePrincipals", f"appId eq '{client_id}'", SP_SELECT))
        add("POST", "/servicePrincipals", {"appId": client_id, "tags": _tags(context, kind),
                                          "appRoleAssignmentRequired": kind == "spa"},
            when="no service principal; reject foreign or ambiguous matches", result=kind + ".service_principal_id")
        add("PATCH", f"/servicePrincipals/{principal_id}", {"appRoleAssignmentRequired": kind == "spa"},
            when="assignment-required setting differs")
        for collection, identity_id in (("applications", object_id), ("servicePrincipals", principal_id)):
            add("GET", f"/{collection}/{identity_id}/owners?$select=id")
            add("POST", f"/{collection}/{identity_id}/owners/$ref",
                {"@odata.id": f"{GRAPH}/directoryObjects/{_uuid(config['owner_id'], 'owner_id')}"},
                when="configured owner not already present")
        if kind == "spa":
            path = f"/servicePrincipals/{principal_id}/appRoleAssignedTo"
            add("GET", path)
            add("DELETE", path + "/${assignment.id}", when="existing User/Admin role assignment is absent from config")
            for assignment in _assignments(config):
                collection = "users" if assignment["principal_type"] == "User" else "groups"
                add("GET", f"/{collection}/{assignment['principal_id']}?$select=id")
                add("POST", path, {"principalId": _uuid(assignment["principal_id"], "principal_id"),
                                  "resourceId": principal_id,
                                  "appRoleId": str(uuid.uuid5(_namespace(context), f"role:{assignment['role']}"))},
                    when="assignment not already present")
        else:
            binding = _binding(bindings, kind)
            for part in ("identity_principal_id", "runtime_identity_principal_id"):
                if binding.get(part):
                    add("GET", f"/servicePrincipals/{binding[part]}?$select=id,appId,servicePrincipalType")
            path = f"/applications/{object_id}/federatedIdentityCredentials"
            add("GET", path)
            add("DELETE", path + "/${credential.id}",
                when="canonical gateway/runtime credential differs or runtime binding was removed")
            for fic in _federation(context, kind, binding):
                add("POST", path, fic, when="canonical credential not already present")
    return calls


def _execute(config: dict, bindings: dict, state_path: Path, redirect_uri: str, *, destroy: bool) -> dict | None:
    context = _validate(config, None if destroy else bindings, redirect_uri)
    state = _load(state_path, context)
    if destroy and not state_path.exists():
        return None
    reconciler = None
    try:
        with GraphClient(context["tenant_id"], context["subscription_id"]) as graph:
            reconciler = _Reconciler(graph, config, bindings, state_path, state)
            result = reconciler.destroy() if destroy else reconciler.reconcile(redirect_uri)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        manifest_path = state_path.with_name(f"{state_path.stem}.admin-request.json")
        attempts = reconciler.calls if reconciler else []
        _write(manifest_path, {
            "operation": "destroy" if destroy else "reconcile",
            "inputs": copy.deepcopy(config), "bindings": copy.deepcopy(bindings),
            "effective_assignments": copy.deepcopy(_assignments(config)),
            "redirect_uri": redirect_uri,
            "graph_base_url": GRAPH,
            "reference_syntax": "${identity.field} refers to a verified existing object or a preceding Graph creation result. "
                                "Application result id maps to object_id and appId maps to client_id; service principal "
                                "result id maps to service_principal_id. GET collections must follow @odata.nextLink. "
                                "Re-read state and discovery before executing.",
            "permission_guidance": "An administrator must authorize/run these Graph operations with appropriate Entra roles "
                                   "and delegated permissions (Application.ReadWrite.All; AppRoleAssignment.ReadWrite.All "
                                   "for assignments; User.Read.All/Group.Read.All for configured principal lookups). "
                                   "Organization validation reads only the tenant ID. "
                                   "No application secrets or Agent365 permissions are requested.",
            "failed_graph_call": attempts[-1] if attempts else None,
            "attempted_graph_calls": attempts,
            "proposed_graph_calls": _proposed_calls(config, bindings, state, redirect_uri, destroy),
        })
        raise GraphPermissionsError(
            f"Microsoft Graph permissions denied (HTTP 403). Admin request: {manifest_path}. "
            "Reconciliation did not complete; owned partial creations are retained in state."
        ) from exc
    state_path.with_name(f"{state_path.stem}.admin-request.json").unlink(missing_ok=True)
    return result


def reconcile_identities(config: dict, bindings: dict, state_path: Path, *, redirect_uri: str = "") -> dict:
    """Return spa/channel/agents IDs after successful reconciliation.

    An empty redirect_uri preserves existing SPA redirects. Omitting assignments
    grants owner_id Admin; explicit assignments (including []) are authoritative
    for User/Admin grants. The SPA preauthorizes its own delegated scope after
    publishing it. Worker removal is explicit via destroy_identities; state
    retains removed worker records so their resources remain cleanable.
    State additionally stores environment, tenant_id and subscription_id and
    may be partial; only a successful return indicates reconciliation completed.
    """
    return _execute(config, bindings, Path(state_path), redirect_uri, destroy=False)


def destroy_identities(config: dict, state_path: Path) -> None:
    """Delete only state-tracked applications and their verified, tagged service principals."""
    _execute(config, {}, Path(state_path), "", destroy=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--redirect-uri", default="")
    parser.add_argument("--destroy", action="store_true")
    args = parser.parse_args(argv)
    try:
        with args.config.open("rb") as stream:
            config = tomllib.load(stream)
        bindings = json.loads(args.bindings.read_text(encoding="utf-8"))
        if args.destroy:
            destroy_identities(config, args.state)
            print("Owned simulator identities destroyed.")
        else:
            print(json.dumps(reconcile_identities(config, bindings, args.state, redirect_uri=args.redirect_uri), indent=2))
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
