from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from scripts import simulator_preflight as preflight


SUBSCRIPTION = "22222222-2222-2222-2222-222222222222"
TENANT = "11111111-1111-1111-1111-111111111111"
OWNER = "33333333-3333-3333-3333-333333333333"
CENTRAL_SUBSCRIPTION = "44444444-4444-4444-4444-444444444444"
BOUNDARY = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/customer"
VNET = BOUNDARY + "/providers/Microsoft.Network/virtualNetworks/customer"
SANDBOX = VNET + "/subnets/sandbox"
ENDPOINTS = VNET + "/subnets/endpoints"
WORKSPACE = BOUNDARY + "/providers/Microsoft.OperationalInsights/workspaces/customer"
INSIGHTS = BOUNDARY + "/providers/Microsoft.Insights/components/customer"
AMPLS = BOUNDARY + "/providers/Microsoft.Insights/privateLinkScopes/customer"
ZONES = (*preflight.DNS_ZONES, "privatelink.swedencentral.azurecontainerapps.io")


def resource(resource_id: str, resource_type: str, **properties) -> dict:
    return {"id": resource_id, "name": resource_id.rsplit("/", 1)[-1], "type": resource_type,
            "location": "swedencentral", **properties}


class SimulatorPreflightTests(unittest.TestCase):
    def setUp(self):
        from scripts.deploy_simulator_environment import environment_root

        self.state_environment = patch.dict(os.environ, {
            "AUTOPILOTS_STATE_ROOT": str(Path(__file__).resolve().parent / "preflight-state-fixture"),
        })
        self.state_environment.start()
        self.addCleanup(self.state_environment.stop)
        self.local_files = {}
        self.local_reads = []
        self.local_reader = patch.object(Path, "read_text", autospec=True, side_effect=self.read_local)
        self.local_reader.start()
        self.addCleanup(self.local_reader.stop)
        self.config = {
            "environment": "training-agent-1", "stack_name": "agent-1", "subscription_id": SUBSCRIPTION,
            "tenant_id": TENANT, "owner_id": OWNER, "auth_mode": "entra", "model_capacity": 15,
            "role": {"blueprint": "example"},
            "images": {
                "schema": 1, "source_commit": "a" * 40,
                "images": {role: f"ghcr.io/example/{role}@sha256:" + "b" * 64
                           for role in ("runtime", "gateway", "simulator")},
            },
            "platform": {
                "existing_resource_group_name": "customer", "existing_sandbox_subnet_id": SANDBOX,
                "existing_private_endpoint_subnet_id": ENDPOINTS,
                "private_dns_zone_ids": {
                    zone: f"/subscriptions/{CENTRAL_SUBSCRIPTION}/resourceGroups/dns"
                          f"/providers/Microsoft.Network/privateDnsZones/{zone}"
                    for zone in ZONES
                },
            },
        }
        self.account = {"id": SUBSCRIPTION, "tenantId": TENANT, "state": "Enabled"}
        self.provider_state = "Registered"
        self.provider_states = {}
        self.failures = {}
        self.resources = {
            BOUNDARY: resource(BOUNDARY, "Microsoft.Resources/resourceGroups"),
            VNET: resource(VNET, "Microsoft.Network/virtualNetworks"),
            SANDBOX: resource(SANDBOX, "Microsoft.Network/virtualNetworks/subnets", properties={
                "addressPrefix": "10.0.0.0/26",
                "delegations": [{"properties": {"serviceName": "Microsoft.App/environments"}}],
            }),
            ENDPOINTS: resource(ENDPOINTS, "Microsoft.Network/virtualNetworks/subnets", properties={
                "addressPrefixes": ["10.0.1.0/28"], "delegations": [],
            }),
            WORKSPACE: resource(WORKSPACE, "Microsoft.OperationalInsights/workspaces"),
            INSIGHTS: resource(INSIGHTS, "Microsoft.Insights/components",
                               properties={"WorkspaceResourceId": WORKSPACE}),
            AMPLS: resource(AMPLS, "Microsoft.Insights/privateLinkScopes"),
        }
        for name, resource_id in self.config["platform"]["private_dns_zone_ids"].items():
            self.resources[resource_id] = resource(resource_id, "Microsoft.Network/privateDnsZones")
        self.usage = {"value": [{
            "name": {"value": "OpenAI.GlobalStandard.gpt-6.1-sol"}, "unit": "Count",
            "limit": 100, "currentValue": 10,
        }]}
        self.models = [{"model": {
            "format": "OpenAI", "name": "gpt-6.1-sol", "version": "2026-09-29",
            "skus": [{"name": "GlobalStandard"}],
        }, "skuName": "S0"}]
        self.commands = []
        self.command = patch.object(preflight, "run", side_effect=self.azure_read)
        self.command.start()
        self.addCleanup(self.command.stop)

    def read_local(self, path, *args, **kwargs):
        key = str(path)
        self.local_reads.append(key)
        if key not in self.local_files:
            raise FileNotFoundError(key)
        return self.local_files[key]

    def azure_read(self, arguments, *, capture, check):
        self.commands.append(arguments)
        self.assertEqual(arguments[0], "az")
        self.assertTrue(capture)
        self.assertFalse(check)
        self.assertEqual(arguments[-3:], ["--only-show-errors", "-o", "json"])
        if arguments[1:3] == ["account", "show"]:
            key, payload = "account", self.account
        elif arguments[1:3] == ["provider", "show"]:
            key = arguments[arguments.index("--namespace") + 1]
            payload = {"registrationState": self.provider_states.get(key, self.provider_state)}
        elif arguments[1:4] == ["cognitiveservices", "model", "list"]:
            key = "models:" + arguments[arguments.index("--location") + 1]
            payload = self.models
        else:
            self.assertEqual(arguments[1:4], ["rest", "--method", "get"])
            url = arguments[arguments.index("--url") + 1]
            self.assertTrue(url.startswith("https://management.azure.com/"))
            self.assertIn("?api-version=", url)
            key = url.removeprefix("https://management.azure.com").split("?")[0]
            payload = self.usage if key.endswith("/usages") else self.resources.get(key)
        error = self.failures.get(key)
        if error or payload is None:
            return subprocess.CompletedProcess(arguments, 1, "", error or "ResourceNotFound")
        return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")

    def test_private_defaults_are_read_only_safe_and_do_not_mutate_configuration(self):
        self.config["demo_access_code"] = "never-report-this"
        self.config["platform"]["ignored_secret"] = "never-report-this-either"
        original = deepcopy(self.config)
        with patch.object(Path, "mkdir", side_effect=AssertionError("filesystem write")), patch.object(
            Path, "write_text", side_effect=AssertionError("filesystem write")
        ), patch("builtins.open", side_effect=AssertionError("filesystem access")):
            report, = preflight.plan([self.config])
        self.assertEqual(report["network_mode"], "private")
        self.assertEqual(report["resource_group"]["mode"], "existing")
        self.assertEqual(report["regions"], {"infrastructure": "swedencentral", "ai": "northeurope"})
        self.assertEqual(report["model_quota"]["status"], "verified")
        self.assertEqual(report["model_quota"]["free_capacity"], 90)
        self.assertEqual(report["private_dns_zone_ids"], original["platform"]["private_dns_zone_ids"])
        self.assertNotIn("never-report-this", json.dumps(report))
        self.assertEqual(self.config, original)
        self.assertTrue(any("no Entra calls" in warning for warning in report["warnings"]))
        self.assertTrue(all(command[1:3] in (["account", "show"], ["provider", "show"])
                            or command[1:4] == ["cognitiveservices", "model", "list"]
                            or command[1:4] == ["rest", "--method", "get"] for command in self.commands))

    def test_shared_dns_subnet_account_and_quota_reads_are_cached_per_invocation(self):
        other = deepcopy(self.config)
        other.update(environment="training-agent-2", stack_name="agent-2", model_capacity=25)
        reports = preflight.plan([self.config, other])
        self.assertTrue(all(report["model_quota"]["requested_capacity"] == 40 for report in reports))
        for zone_id in self.config["platform"]["private_dns_zone_ids"].values():
            self.assertEqual(sum(zone_id in " ".join(command) for command in self.commands), 1)
        self.assertEqual(sum("/usages?" in " ".join(command) for command in self.commands), 1)
        self.assertEqual(sum(command[1:3] == ["account", "show"] for command in self.commands), 1)
        self.assertEqual(sum(command[1:4] == ["cognitiveservices", "model", "list"] for command in self.commands), 1)
        self.assertEqual(sum(VNET + "?" in " ".join(command) for command in self.commands), 1)
        count = len(self.commands)
        preflight.plan([self.config])
        self.assertGreater(len(self.commands), count)

    def shared_stacks(self):
        configs = []
        for name in [f"agent-{index}" for index in range(1, 7)] + ["agent-dev"]:
            config = deepcopy(self.config)
            config.update(
                environment="training-" + name, stack_name=name, deployment_environment="training",
                foundry_mode="shared", network_mode="private",
                shared_foundry={"mode": "create", "existing_resource_group_name": "central",
                                "existing_private_endpoint_subnet_id": ENDPOINTS, "model_capacity": 30},
            )
            configs.append(config)
        central_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/central"
        self.resources[central_id] = resource(central_id, "Microsoft.Resources/resourceGroups")
        return configs

    def test_seven_shared_stacks_request_one_pool_and_offline_plan_makes_no_azure_calls(self):
        configs = self.shared_stacks()
        reports = preflight.plan(configs, offline=True)
        self.assertEqual(self.commands, [])
        self.assertEqual([report["stack_name"] for report in reports][-1], "agent-dev")
        self.assertEqual(len({report["state_root"] for report in reports}), 7)
        self.assertEqual(len({report["shared_state_root"] for report in reports}), 1)
        self.usage["value"][0].update(limit=1000, currentValue=955)
        reports = preflight.plan(configs)
        self.assertTrue(all(report["model_quota"]["requested_capacity"] == 30 for report in reports))
        self.assertTrue(all(report["model"]["capacity"] == 30 for report in reports))
        self.assertEqual(sum("/usages?" in " ".join(command) for command in self.commands), 1)
        self.assertEqual(sum(command[1:4] == ["cognitiveservices", "model", "list"] for command in self.commands), 1)

    def test_existing_shared_account_does_not_request_or_credit_model_quota(self):
        configs = self.shared_stacks()
        account_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/central/providers/Microsoft.CognitiveServices/accounts/shared"
        deployment_id = account_id + "/deployments/pooled"
        self.resources[account_id] = resource(account_id, "Microsoft.CognitiveServices/accounts", properties={
            "allowProjectManagement": True, "disableLocalAuth": True,
            "publicNetworkAccess": "Disabled", "customSubDomainName": "shared",
        })
        self.resources[deployment_id] = resource(
            deployment_id, "Microsoft.CognitiveServices/accounts/deployments",
            sku={"name": "GlobalStandard", "capacity": 30},
            properties={"provisioningState": "Succeeded", "model": {
                "format": "OpenAI", "name": "gpt-6.1-sol", "version": "2026-09-29",
            }},
        )
        for config in configs:
            config["shared_foundry"] = {
                "mode": "existing", "account_resource_id": account_id, "model_deployment_name": "pooled",
            }
        reports = preflight.plan(configs)
        self.assertTrue(all(report["model_quota"]["status"] == "externally_owned" for report in reports))
        self.assertTrue(all(report["model_capacity_credit"]["owned_capacity"] == 0 for report in reports))
        self.assertFalse(any("/usages?" in " ".join(command) for command in self.commands))
        self.resources[account_id]["properties"]["publicNetworkAccess"] = "Enabled"
        with self.assertRaisesRegex(ValueError, "private-only"):
            preflight.plan(configs)

    def test_shared_capacity_credit_requires_exact_central_state_and_never_stack_state(self):
        from scripts.deploy_simulator_environment import shared_environment_root, shared_workspace, shared_ownership, state_path

        configs = self.shared_stacks()
        central = deepcopy(configs[0])
        central["environment"] = "training"
        central["platform"]["existing_resource_group_name"] = "central"
        central["platform"]["location"] = "swedencentral"
        ownership_path, platform_path, _, _ = self.own_stack(central, capacity=25)
        central_root = shared_environment_root(central)
        self.local_files[str(central_root / "environment.json")] = json.dumps(shared_ownership(central))
        self.local_files[str(state_path(central_root, "foundry", shared_workspace(central)))] = self.local_files.pop(platform_path)
        self.local_files.pop(ownership_path)
        self.usage["value"][0].update(limit=1000, currentValue=995)
        reports = preflight.plan(configs)
        self.assertTrue(all(report["model_quota"]["owned_capacity"] == 25 for report in reports))
        self.assertTrue(all(report["model_quota"]["incremental_capacity"] == 5 for report in reports))
        ownership = json.loads(self.local_files[str(central_root / "environment.json")])
        ownership["existing_resource_group_name"] = "wrong"
        self.local_files[str(central_root / "environment.json")] = json.dumps(ownership)
        with self.assertRaisesRegex(ValueError, "Shared Foundry ownership"):
            preflight.plan(configs)

    def test_wrong_sandbox_delegation_blocks(self):
        self.resources[SANDBOX]["properties"]["delegations"][0]["properties"]["serviceName"] = "Microsoft.Web/serverFarms"
        with self.assertRaisesRegex(ValueError, "delegated only to Microsoft.App/environments"):
            preflight.plan([self.config])

    def test_small_or_ipv6_only_sandbox_subnet_blocks(self):
        for prefix in ("10.0.0.0/28", "fd00::/64"):
            with self.subTest(prefix=prefix):
                self.resources[SANDBOX]["properties"]["addressPrefix"] = prefix
                with self.assertRaisesRegex(ValueError, "/27 or larger"):
                    preflight.plan([self.config])

    def test_27_sandbox_warns_and_delegated_endpoint_subnet_blocks(self):
        self.resources[SANDBOX]["properties"]["addressPrefix"] = "10.0.0.0/27"
        report, = preflight.plan([self.config])
        self.assertTrue(any("/26 or larger" in warning for warning in report["warnings"]))
        self.resources[ENDPOINTS]["properties"]["delegations"] = [{"properties": {"serviceName": "other"}}]
        with self.assertRaisesRegex(ValueError, "no delegation"):
            preflight.plan([self.config])

    def test_unreadable_dns_is_blocking_even_for_access_denial(self):
        zone_id = next(iter(self.config["platform"]["private_dns_zone_ids"].values()))
        self.failures[zone_id] = "(AuthorizationFailed) forbidden private-token"
        with self.assertRaisesRegex(RuntimeError, "Private DNS zone.*unreadable") as error:
            preflight.plan([self.config])
        self.assertNotIn("private-token", str(error.exception))

    def test_tenant_or_subscription_mismatch_blocks_before_resource_reads(self):
        for field in ("tenantId", "id"):
            with self.subTest(field=field):
                self.account = {**self.account, field: CENTRAL_SUBSCRIPTION}
                with self.assertRaisesRegex(ValueError, "tenant/subscription mismatch"):
                    preflight.plan([self.config])
                self.account = {"id": SUBSCRIPTION, "tenantId": TENANT, "state": "Enabled"}
        self.assertTrue(all(command[1:3] == ["account", "show"] for command in self.commands))

    def test_missing_group_and_subnet_are_blocking(self):
        for resource_id, label in ((BOUNDARY, "resource group"), (SANDBOX, "Sandbox subnet")):
            with self.subTest(resource_id=resource_id):
                self.failures[resource_id] = "ResourceNotFound"
                with self.assertRaisesRegex(RuntimeError, label + ".*unreadable"):
                    preflight.plan([self.config])
                self.failures.clear()

    def test_vnet_location_must_match_infrastructure_region(self):
        self.resources[VNET]["location"] = "northeurope"
        with self.assertRaisesRegex(ValueError, "VNet location"):
            preflight.plan([self.config])

    def test_dns_keys_actual_name_and_type_must_match(self):
        zone_id = next(iter(self.config["platform"]["private_dns_zone_ids"].values()))
        for field, value in (("name", "privatelink.wrong.example"), ("type", "Microsoft.Network/dnsZones")):
            with self.subTest(field=field):
                original = self.resources[zone_id][field]
                self.resources[zone_id][field] = value
                with self.assertRaisesRegex(ValueError, "Private DNS zone"):
                    preflight.plan([self.config])
                self.resources[zone_id][field] = original
        del self.config["platform"]["private_dns_zone_ids"][ZONES[0]]
        with self.assertRaisesRegex(ValueError, "missing required private DNS zones"):
            preflight.plan([self.config])

    def test_parent_aca_zone_and_extra_unused_zones_are_read_and_validated(self):
        zones = self.config["platform"]["private_dns_zone_ids"]
        regional = zones.pop(ZONES[-1])
        for name in ("privatelink.azurecontainerapps.io", "privatelink.blob.core.windows.net",
                     "privatelink.vaultcore.azure.net"):
            zone_id = regional.rsplit("/", 1)[0] + "/" + name
            zones[name] = zone_id
            self.resources[zone_id] = resource(zone_id, "Microsoft.Network/privateDnsZones")
        report, = preflight.plan([self.config])
        self.assertEqual(report["private_dns_zone_ids"], zones)
        self.failures[zones["privatelink.vaultcore.azure.net"]] = "403 Forbidden"
        with self.assertRaisesRegex(RuntimeError, "Private DNS zone"):
            preflight.plan([self.config])

    def test_private_plan_without_scheduler_does_not_require_servicebus_dns(self):
        self.config["platform"]["scheduler_enabled"] = False
        zones = self.config["platform"]["private_dns_zone_ids"]
        servicebus_id = zones.pop("privatelink.servicebus.windows.net")
        report, = preflight.plan([self.config])
        self.assertFalse(report["scheduler_enabled"])
        self.assertFalse(any(servicebus_id in " ".join(command) for command in self.commands))
        del self.config["platform"]["scheduler_enabled"]
        with self.assertRaisesRegex(ValueError, "missing required private DNS zones: privatelink.servicebus.windows.net"):
            preflight.plan([self.config])
        self.config["platform"]["scheduler_enabled"] = False
        del zones["privatelink.cognitiveservices.azure.com"]
        with self.assertRaisesRegex(ValueError, "missing required private DNS zones: privatelink.cognitiveservices.azure.com"):
            preflight.plan([self.config])

    def test_create_group_skips_only_group_existence_check(self):
        self.config["resource_group_mode"] = "create"
        self.failures[BOUNDARY] = "ResourceNotFound"
        report, = preflight.plan([self.config])
        self.assertEqual(report["resource_group"]["mode"], "create")
        self.assertFalse(any(BOUNDARY + "?" in " ".join(command) for command in self.commands))
        self.assertTrue(any("/usages?" in " ".join(command) for command in self.commands))
        self.failures[SANDBOX] = "ResourceNotFound"
        with self.assertRaisesRegex(RuntimeError, "Sandbox subnet"):
            preflight.plan([self.config])

    def test_public_profile_is_explicit_and_private_configuration_disagreement_blocks(self):
        self.config["network_mode"] = "public"
        self.config["platform"] = {"existing_resource_group_name": "customer"}
        report, = preflight.plan([self.config])
        self.assertEqual(report["profile"], "PUBLIC")
        self.assertTrue(any("PUBLIC PROFILE" in warning for warning in report["warnings"]))
        self.assertFalse(any("Microsoft.Network/" in " ".join(command) for command in self.commands))
        self.config["platform"]["private_network_enabled"] = True
        with self.assertRaisesRegex(ValueError, "disagree"):
            preflight.plan([self.config])

    def test_monitoring_pair_ampls_types_and_workspace_link_are_validated(self):
        self.config["platform"].update(
            existing_log_analytics_workspace_id=WORKSPACE, existing_application_insights_id=INSIGHTS, ampls_id=AMPLS,
        )
        report, = preflight.plan([self.config])
        self.assertEqual(report["monitoring"]["ampls_id"], AMPLS)
        for resource_id in (WORKSPACE, INSIGHTS, AMPLS):
            with self.subTest(resource_id=resource_id):
                original = self.resources[resource_id]["type"]
                self.resources[resource_id]["type"] = "Microsoft.Network/privateDnsZones"
                with self.assertRaisesRegex(ValueError, "type"):
                    preflight.plan([self.config])
                self.resources[resource_id]["type"] = original
        self.resources[INSIGHTS]["properties"]["WorkspaceResourceId"] = "different"
        with self.assertRaisesRegex(ValueError, "linked to"):
            preflight.plan([self.config])
        del self.config["platform"]["existing_application_insights_id"]
        with self.assertRaisesRegex(ValueError, "both Log Analytics and Application Insights"):
            preflight.plan([self.config])

    def test_quota_compares_sum_not_each_individual_stack(self):
        self.usage["value"][0].update(limit=35, currentValue=10)
        other = {**self.config, "environment": "training-agent-2", "stack_name": "agent-2"}
        with self.assertRaisesRegex(ValueError, "request 30.*25 free"):
            preflight.plan([self.config, other])

    def test_quota_never_guesses_other_sku_model_unit_or_ambiguous_meters(self):
        original = deepcopy(self.usage)
        for change in (
            {"value": [{"name": {"value": "OpenAI.Standard.gpt-6.1-sol"}, "unit": "Count", "limit": 0, "currentValue": 0}]},
            {"value": [{"name": {"value": "OpenAI.GlobalStandard.gpt-6.1-sol-mini"}, "unit": "Count", "limit": 0, "currentValue": 0}]},
            {"value": [{**original["value"][0], "unit": "Tokens"}]},
            {"value": [{**original["value"][0], "limit": "100"}]},
            {"value": original["value"] * 2},
            {**original, "nextLink": "https://management.azure.com/continuation"},
        ):
            with self.subTest(change=change):
                self.usage = change
                report, = preflight.plan([self.config])
                self.assertEqual(report["model_quota"]["status"], "unverified")
                self.assertTrue(any("UNVERIFIED model quota" in warning for warning in report["warnings"]))

    def test_provider_registration_blocks_but_access_denials_are_visible_unverified(self):
        self.provider_state = "NotRegistered"
        with self.assertRaisesRegex(ValueError, "not Registered"):
            preflight.plan([self.config])
        self.provider_state = "Registered"
        self.failures["Microsoft.App"] = "403 Forbidden"
        quota_path = f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.CognitiveServices/locations/northeurope/usages"
        self.failures[quota_path] = "AuthorizationFailed"
        reports = preflight.plan([self.config, {**self.config, "environment": "training-agent-2"}])
        self.assertEqual(reports[0]["providers"]["Microsoft.App"], "unverified")
        self.assertEqual(reports[0]["model_quota"]["status"], "unverified")
        self.assertTrue(any("access denied" in warning for warning in reports[0]["warnings"]))
        self.assertEqual(sum("Microsoft.App" in command for command in self.commands), 2)

    def test_servicebus_provider_is_required_only_if_any_planned_scope_uses_scheduler(self):
        self.config["platform"]["scheduler_enabled"] = False
        self.provider_states["Microsoft.ServiceBus"] = "NotRegistered"
        report, = preflight.plan([self.config])
        self.assertEqual(report["providers"]["Microsoft.ServiceBus"], "not_required")
        self.assertFalse(any("Microsoft.ServiceBus" in command for command in self.commands))
        other = deepcopy(self.config)
        other.update(environment="training-agent-2", stack_name="agent-2")
        other["platform"]["scheduler_enabled"] = True
        with self.assertRaisesRegex(ValueError, "Microsoft.ServiceBus is not Registered"):
            preflight.plan([self.config, other])
        del self.config["platform"]["scheduler_enabled"]
        with self.assertRaisesRegex(ValueError, "Microsoft.ServiceBus is not Registered"):
            preflight.plan([self.config])

    def test_unexpected_api_failure_is_not_downgraded_to_warning(self):
        for key in ("Microsoft.App", f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.CognitiveServices/locations/northeurope/usages"):
            with self.subTest(key=key):
                self.failures[key] = "InternalServerError private-token"
                with self.assertRaises(RuntimeError) as error:
                    preflight.plan([self.config])
                self.assertNotIn("private-token", str(error.exception))
                self.failures.clear()

    def test_invalid_capacity_and_resource_ids_block_without_writes(self):
        for capacity in (0, -1, True, 1.5, "15"):
            with self.subTest(capacity=capacity), self.assertRaisesRegex(ValueError, "positive integer"):
                preflight.plan([{**self.config, "model_capacity": capacity}])
        self.config["platform"]["existing_sandbox_subnet_id"] = "https://example.test/secret"
        with self.assertRaisesRegex(ValueError, "must identify"):
            preflight.plan([self.config])

    def test_prebuilt_plan_needs_no_registry_provider_dns_build_pool_or_data_plane_reads(self):
        self.config["platform"]["scheduler_enabled"] = False
        self.provider_states["Microsoft.ContainerRegistry"] = "NotRegistered"
        report, = preflight.plan([self.config])
        self.assertEqual(report["images"]["images"], self.config["images"]["images"])
        self.assertTrue(any("No ACR, build pool" in warning for warning in report["warnings"]))
        self.assertFalse(any("Microsoft.ContainerRegistry" in " ".join(command) for command in self.commands))
        self.assertFalse(any("azurecr.io" in " ".join(command) for command in self.commands))

    def test_preflight_validates_manifest_before_azure_reads(self):
        self.config["images"]["images"]["runtime"] = "ghcr.io/example/runtime:latest"
        with self.assertRaisesRegex(ValueError, "immutable"):
            preflight.plan([self.config])
        self.assertEqual(self.commands, [])

    def test_regional_availability_uses_actual_model_version_and_deployment_sku_not_deployment_name(self):
        self.config["platform"]["model_deployment_name"] = "classroom-alias"
        report, = preflight.plan([self.config])
        self.assertEqual(report["model"]["name"], "gpt-6.1-sol")
        self.assertEqual(report["model"]["deployment_name"], "classroom-alias")
        self.assertEqual(report["model_availability"], {
            "status": "verified", "region": "northeurope", "model_name": "gpt-6.1-sol",
            "version": "2026-09-29", "sku": "GlobalStandard",
        })
        self.assertEqual(report["model_quota"]["status"], "verified")

    def test_unsupported_regional_model_version_or_sku_blocks_despite_available_quota(self):
        self.usage["value"][0].update(limit=80, currentValue=15)
        original = deepcopy(self.models)
        for catalog in (
            [],
            [{**original[0], "model": {**original[0]["model"], "name": "other-model"}}],
            [{**original[0], "model": {**original[0]["model"], "version": "2026-10-01"}}],
            [{"skuName": "GlobalStandard", "model": {
                **original[0]["model"], "skus": [{"name": "Standard"}],
            }}],
        ):
            with self.subTest(catalog=catalog):
                self.models = catalog
                with self.assertRaisesRegex(ValueError, "Unsupported regional model/version/SKU.*northeurope.*quota"):
                    preflight.plan([self.config])

    def test_regional_model_read_denial_warns_without_treating_quota_as_availability(self):
        self.failures["models:northeurope"] = "403 Forbidden private-token"
        other = {**self.config, "environment": "training-agent-2", "stack_name": "agent-2"}
        reports = preflight.plan([self.config, other])
        for report in reports:
            self.assertEqual(report["model_availability"]["status"], "unverified")
            self.assertEqual(report["model_quota"]["status"], "verified")
            self.assertTrue(any("quota does not establish regional model/version/SKU support" in warning
                                for warning in report["warnings"]))
            self.assertNotIn("private-token", json.dumps(report))
        self.assertEqual(sum(command[1:4] == ["cognitiveservices", "model", "list"] for command in self.commands), 1)

    def test_incomplete_regional_model_metadata_is_explicitly_unverified(self):
        for catalog in (
            {"unexpected": "schema"},
            [{"model": None}],
            [{"model": {"format": "OpenAI", "name": "gpt-6.1-sol"}}],
            [{"model": {"format": "OpenAI", "name": "gpt-6.1-sol", "version": "2026-09-29", "skus": None}}],
        ):
            with self.subTest(catalog=catalog):
                self.models = catalog
                report, = preflight.plan([self.config])
                self.assertEqual(report["model_availability"]["status"], "unverified")
                self.assertTrue(any("regional model catalog metadata is unreadable or incomplete" in warning
                                    for warning in report["warnings"]))

    def test_explicit_region_version_and_sku_are_checked_without_a_runtime_fallback(self):
        self.config["platform"].update(
            location="swedencentral", model_name="gpt-6.1-sol",
            model_version="2026-10-01", model_sku_name="Standard",
        )
        self.models[0]["model"].update(version="2026-10-01", skus=[{"name": "Standard"}])
        original = deepcopy(self.config)
        report, = preflight.plan([self.config])
        self.assertEqual(report["model_availability"]["region"], "swedencentral")
        self.assertEqual(report["model_availability"]["version"], "2026-10-01")
        self.assertEqual(report["model_availability"]["sku"], "Standard")
        self.assertEqual(report["model_availability"]["status"], "verified")
        self.assertEqual(self.config, original)
        command = next(command for command in self.commands if command[1:4] == ["cognitiveservices", "model", "list"])
        self.assertEqual(command[command.index("--location") + 1], "swedencentral")

    def own_stack(self, config, capacity=10):
        from scripts.deploy_simulator_environment import environment_root, state_path

        root = environment_root(config)
        platform_path = state_path(root, "platform", config["environment"])
        group = config["platform"].get("existing_resource_group_name") or "rg-ap-" + config["environment"] + "-abcd"
        boundary = f"/subscriptions/{config['subscription_id']}/resourceGroups/{group}"
        account_id = boundary + "/providers/Microsoft.CognitiveServices/accounts/" + config["environment"]
        deployment_name = config["platform"].get("model_deployment_name", "gpt-6-1-sol")
        deployment_id = account_id + "/deployments/" + deployment_name
        ownership_path = root / "environment.json"
        self.local_files[str(ownership_path)] = json.dumps({
            field: config[field] for field in ("environment", "subscription_id", "tenant_id", "owner_id")
        })
        state = {"version": 4, "resources": [
            {"mode": "managed", "type": "azapi_resource", "name": "foundry", "instances": [{"attributes": {
                "id": account_id, "type": "Microsoft.CognitiveServices/accounts@2025-06-01",
            }}]},
            {"mode": "managed", "type": "azapi_resource", "name": "foundry_deployment", "instances": [{"attributes": {
                "id": deployment_id, "parent_id": account_id, "name": deployment_name,
                "type": "Microsoft.CognitiveServices/accounts/deployments@2025-06-01",
                "body": {"sku": {"capacity": 9999}},
            }}]},
        ]}
        if not config["platform"].get("existing_resource_group_name"):
            state["resources"].append({
                "mode": "managed", "type": "azurerm_resource_group", "name": "main", "instances": [{"attributes": {
                    "id": boundary, "name": group,
                }}],
            })
        self.local_files[str(platform_path)] = json.dumps(state)
        self.resources[account_id] = resource(account_id, "Microsoft.CognitiveServices/accounts",
                                               location=config["platform"].get("location", "northeurope"))
        self.resources[deployment_id] = resource(deployment_id, "Microsoft.CognitiveServices/accounts/deployments",
            sku={"name": config["platform"].get("model_sku_name", "GlobalStandard"), "capacity": capacity},
            properties={"provisioningState": "Succeeded", "model": {
                "format": "OpenAI", "name": config["platform"].get("model_name", "gpt-6.1-sol"),
                "version": config["platform"].get("model_version", "2026-09-29"),
            }})
        return str(ownership_path), str(platform_path), account_id, deployment_id

    def six_stacks(self):
        configs = []
        for index in range(1, 7):
            config = deepcopy(self.config)
            config.update(environment=f"training-agent-{index}", stack_name=f"agent-{index}", model_capacity=10)
            configs.append(config)
        return configs

    def test_six_new_stacks_fail_but_one_exact_owned_stack_credits_only_incremental_capacity(self):
        configs = self.six_stacks()
        configs[0]["owned_capacity"] = 10000
        self.usage["value"][0].update(limit=65, currentValue=10)
        with self.assertRaisesRegex(ValueError, "60 capacity units.*0 exact-state-owned, 60 incremental.*55 free"):
            preflight.plan(configs)
        ownership_path, platform_path, account_id, deployment_id = self.own_stack(configs[1])
        reports = preflight.plan(configs)
        self.assertTrue(all(report["model_quota"]["requested_capacity"] == 60 for report in reports))
        self.assertTrue(all(report["model_quota"]["owned_capacity"] == 10 for report in reports))
        self.assertTrue(all(report["model_quota"]["incremental_capacity"] == 50 for report in reports))
        self.assertEqual(reports[1]["model_capacity_credit"]["deployment_id"], deployment_id)
        self.assertEqual(reports[1]["model_capacity_credit"]["state_path"], platform_path)
        self.assertEqual(reports[1]["model_capacity_credit"]["ownership_path"], ownership_path)
        self.assertIn("preflight-state-fixture", platform_path)
        self.assertTrue(all(path.startswith(os.environ["AUTOPILOTS_STATE_ROOT"]) for path in self.local_reads))
        self.assertEqual(sum(deployment_id + "?" in " ".join(command) for command in self.commands), 1)
        self.assertEqual(sum(account_id + "?" in " ".join(command) for command in self.commands), 1)

    def test_six_unchanged_owned_stacks_pass_with_only_five_free_units(self):
        configs = self.six_stacks()
        for config in configs:
            self.own_stack(config)
        self.usage["value"][0].update(limit=65, currentValue=60)
        reports = preflight.plan(configs)
        self.assertTrue(all(report["model_quota"]["owned_capacity"] == 60 for report in reports))
        self.assertTrue(all(report["model_quota"]["incremental_capacity"] == 0 for report in reports))
        self.assertTrue(all(report["model_quota"]["status"] == "verified" for report in reports))

    def test_owned_capacity_does_not_hide_scale_up_or_spend_future_scale_down(self):
        self.config["model_capacity"] = 20
        self.own_stack(self.config, capacity=10)
        self.usage["value"][0].update(limit=15, currentValue=10)
        with self.assertRaisesRegex(ValueError, "10 exact-state-owned, 10 incremental.*5 free"):
            preflight.plan([self.config])
        self.config["model_capacity"] = 5
        new = deepcopy(self.config)
        new.update(environment="training-agent-2", stack_name="agent-2", model_capacity=10)
        with self.assertRaisesRegex(ValueError, "5 exact-state-owned, 10 incremental.*5 free"):
            preflight.plan([self.config, new])

    def test_unrelated_or_nonmatching_live_deployments_receive_no_credit(self):
        self.config["model_capacity"] = 10
        _, platform_path, account_id, deployment_id = self.own_stack(self.config)
        original_live = deepcopy(self.resources[deployment_id])
        for change in ("sku", "model", "version", "region", "name", "provisioning"):
            with self.subTest(change=change):
                self.resources[deployment_id] = deepcopy(original_live)
                self.resources[account_id]["location"] = "northeurope"
                if change == "sku":
                    self.resources[deployment_id]["sku"]["name"] = "Standard"
                elif change == "model":
                    self.resources[deployment_id]["properties"]["model"]["name"] = "other-model"
                elif change == "version":
                    self.resources[deployment_id]["properties"]["model"]["version"] = "other-version"
                elif change == "region":
                    self.resources[account_id]["location"] = "swedencentral"
                elif change == "name":
                    self.resources[deployment_id]["name"] = "other-deployment"
                else:
                    self.resources[deployment_id]["properties"]["provisioningState"] = "Updating"
                report, = preflight.plan([self.config])
                self.assertEqual(report["model_capacity_credit"]["owned_capacity"], 0)
                self.assertEqual(report["model_quota"]["incremental_capacity"], 10)
        state = json.loads(self.local_files[platform_path])
        state["resources"][1]["name"] = "unrelated"
        self.local_files[platform_path] = json.dumps(state)
        report, = preflight.plan([self.config])
        self.assertEqual(report["model_capacity_credit"]["status"], "no_current_owned_deployment")
        self.assertEqual(report["model_quota"]["owned_capacity"], 0)

    def test_state_and_ownership_boundary_errors_are_explicit(self):
        ownership_path, platform_path, _, _ = self.own_stack(self.config)
        original_ownership = self.local_files[ownership_path]
        original_state = self.local_files[platform_path]
        for field in ("environment", "subscription_id", "tenant_id", "owner_id"):
            with self.subTest(field=field):
                owner = json.loads(original_ownership)
                owner[field] = "different"
                self.local_files[ownership_path] = json.dumps(owner)
                with self.assertRaisesRegex(ValueError, "Environment ownership " + field):
                    preflight.plan([self.config])
        self.local_files[ownership_path] = original_ownership
        for malformed in ("not-json", json.dumps({"version": 4, "resources": "invalid"})):
            with self.subTest(malformed=malformed):
                self.local_files[platform_path] = malformed
                with self.assertRaisesRegex(ValueError, "platform Terraform state"):
                    preflight.plan([self.config])
        state = json.loads(original_state)
        state["resources"][1]["instances"][0]["attributes"]["id"] = WORKSPACE
        self.local_files[platform_path] = json.dumps(state)
        with self.assertRaisesRegex(ValueError, "State-owned deployment ID"):
            preflight.plan([self.config])
        self.local_files[platform_path] = original_state
        del self.local_files[ownership_path]
        with self.assertRaisesRegex(ValueError, "without its environment ownership"):
            preflight.plan([self.config])

    def test_create_mode_credits_only_its_exact_managed_group_and_duplicate_credit_is_blocked(self):
        self.config["resource_group_mode"] = "create"
        self.config["platform"].pop("existing_resource_group_name")
        self.own_stack(self.config)
        report, = preflight.plan([self.config])
        self.assertEqual(report["model_capacity_credit"]["owned_capacity"], 10)
        self.assertEqual(report["model_capacity_credit"]["resource_group_name"], "rg-ap-training-agent-1-abcd")
        with self.assertRaisesRegex(ValueError, "cannot receive capacity credit"):
            preflight.plan([self.config, self.config])


if __name__ == "__main__":
    unittest.main()
