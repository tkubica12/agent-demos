from __future__ import annotations

from copy import deepcopy
import json
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
BUILD_SUBNET = VNET + "/subnets/build"
SOURCE_REGISTRY = BOUNDARY + "/providers/Microsoft.ContainerRegistry/registries/artifacts"
SOURCE_SERVER = "artifacts.azurecr.io"
WORKSPACE = BOUNDARY + "/providers/Microsoft.OperationalInsights/workspaces/customer"
INSIGHTS = BOUNDARY + "/providers/Microsoft.Insights/components/customer"
AMPLS = BOUNDARY + "/providers/Microsoft.Insights/privateLinkScopes/customer"
ZONES = (*preflight.DNS_ZONES, "privatelink.swedencentral.azurecontainerapps.io")


def resource(resource_id: str, resource_type: str, **properties) -> dict:
    return {"id": resource_id, "name": resource_id.rsplit("/", 1)[-1], "type": resource_type,
            "location": "swedencentral", **properties}


class SimulatorPreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "environment": "training-agent-1", "stack_name": "agent-1", "subscription_id": SUBSCRIPTION,
            "tenant_id": TENANT, "owner_id": OWNER, "auth_mode": "entra", "model_capacity": 15,
            "role": {"blueprint": "example"},
            "build": {"mode": "source"},
            "platform": {
                "existing_resource_group_name": "customer", "existing_sandbox_subnet_id": SANDBOX,
                "existing_private_endpoint_subnet_id": ENDPOINTS,
                "acr_agent_pool_subnet_id": BUILD_SUBNET,
                "private_dns_zone_ids": {
                    zone: f"/subscriptions/{CENTRAL_SUBSCRIPTION}/resourceGroups/dns"
                          f"/providers/Microsoft.Network/privateDnsZones/{zone}"
                    for zone in ZONES
                },
            },
        }
        self.account = {"id": SUBSCRIPTION, "tenantId": TENANT, "state": "Enabled"}
        self.provider_state = "Registered"
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
            BUILD_SUBNET: resource(BUILD_SUBNET, "Microsoft.Network/virtualNetworks/subnets", properties={
                "addressPrefix": "10.0.2.0/26", "delegations": [],
            }),
            SOURCE_REGISTRY: resource(SOURCE_REGISTRY, "Microsoft.ContainerRegistry/registries", properties={
                "loginServer": SOURCE_SERVER, "publicNetworkAccess": "Disabled",
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
        self.commands = []
        self.command = patch.object(preflight, "run", side_effect=self.azure_read)
        self.command.start()
        self.addCleanup(self.command.stop)

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
            payload = {"registrationState": self.provider_state}
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
        self.assertEqual(sum(VNET + "?" in " ".join(command) for command in self.commands), 1)
        count = len(self.commands)
        preflight.plan([self.config])
        self.assertGreater(len(self.commands), count)

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
        del zones["privatelink.azurecr.io"]
        with self.assertRaisesRegex(ValueError, "missing required private DNS zones: privatelink.azurecr.io"):
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

    def import_settings(self):
        return {
            "mode": "import", "allow_trusted_service_import": True,
            "source_registry_id": SOURCE_REGISTRY, "source_registry_server": SOURCE_SERVER,
            **{field: SOURCE_SERVER + "/demo/" + field + "@sha256:" + digest * 64
               for field, digest in (("runtime_image", "a"), ("simulator_image", "b"), ("gateway_image", "c"))},
        }

    def enable_import(self):
        self.config["build"] = self.import_settings()
        self.config["platform"]["acr_trusted_services_bypass_enabled"] = True

    def test_explicit_import_reports_immutable_artifacts_without_data_plane_reads(self):
        self.enable_import()
        del self.config["platform"]["acr_agent_pool_subnet_id"]
        other = deepcopy(self.config)
        other.update(environment="training-agent-2", stack_name="agent-2")
        original = deepcopy(self.config)
        reports = preflight.plan([self.config, other])
        self.assertEqual(self.config, original)
        self.assertEqual(reports[0]["build"]["mode"], "import")
        for field in ("runtime_image", "simulator_image", "gateway_image"):
            self.assertEqual(reports[0]["build"][field], self.config["build"][field])
        self.assertIn("image pulls are unverified", reports[0]["build"]["artifact_verification"])
        self.assertTrue(any("No registry data-plane queries" in warning for warning in reports[0]["warnings"]))
        self.assertEqual(sum(SOURCE_REGISTRY + "?" in " ".join(command) for command in self.commands), 1)
        self.assertTrue(all(command[1:3] in (["account", "show"], ["provider", "show"])
                            or command[1:4] == ["rest", "--method", "get"] for command in self.commands))

    def test_import_requires_every_immutable_same_server_image(self):
        self.enable_import()
        for field in ("runtime_image", "simulator_image", "gateway_image"):
            for value in (None, SOURCE_SERVER + "/demo/image:latest",
                          "other.azurecr.io/demo/image@sha256:" + "a" * 64,
                          SOURCE_SERVER + "/demo/image@sha256:" + "a" * 63):
                with self.subTest(field=field, value=value):
                    self.config["build"] = {**self.import_settings(), field: value}
                    with self.assertRaisesRegex(ValueError, "build." + field):
                        preflight.plan([self.config])
        self.assertEqual(self.commands, [])

    def test_private_source_registry_public_access_must_be_disabled(self):
        self.enable_import()
        for value in ("Enabled", None):
            with self.subTest(value=value):
                self.resources[SOURCE_REGISTRY]["properties"]["publicNetworkAccess"] = value
                with self.assertRaisesRegex(ValueError, "publicNetworkAccess.*Disabled"):
                    preflight.plan([self.config])
        self.config.update(network_mode="public")
        self.config["build"]["mode"] = "import"
        self.config["platform"] = {"existing_resource_group_name": "customer", "acr_trusted_services_bypass_enabled": True}
        self.resources[SOURCE_REGISTRY]["properties"]["publicNetworkAccess"] = "Enabled"
        report, = preflight.plan([self.config])
        self.assertEqual(report["build"]["mode"], "import")

    def test_source_registry_must_have_matching_id_type_login_server_and_be_readable(self):
        self.enable_import()
        for field, value in (("id", WORKSPACE), ("type", "Microsoft.Insights/components")):
            with self.subTest(field=field):
                original = self.resources[SOURCE_REGISTRY][field]
                self.resources[SOURCE_REGISTRY][field] = value
                with self.assertRaisesRegex(ValueError, "Source registry.*ID or type"):
                    preflight.plan([self.config])
                self.resources[SOURCE_REGISTRY][field] = original
        self.resources[SOURCE_REGISTRY]["properties"]["loginServer"] = "other.azurecr.io"
        with self.assertRaisesRegex(ValueError, "loginServer"):
            preflight.plan([self.config])
        self.failures[SOURCE_REGISTRY] = "403 Forbidden"
        with self.assertRaisesRegex(RuntimeError, "Source registry.*unreadable"):
            preflight.plan([self.config])

    def test_private_source_build_requires_dedicated_non_delegated_subnet(self):
        for shared_subnet in (SANDBOX, ENDPOINTS.upper()):
            with self.subTest(shared_subnet=shared_subnet):
                self.config["platform"]["acr_agent_pool_subnet_id"] = shared_subnet
                with self.assertRaisesRegex(ValueError, "dedicated and different"):
                    preflight.plan([self.config])
        self.config["platform"]["acr_agent_pool_subnet_id"] = BUILD_SUBNET
        self.resources[BUILD_SUBNET]["properties"]["delegations"] = [{"properties": {"serviceName": "Microsoft.App/environments"}}]
        with self.assertRaisesRegex(ValueError, "ACR build-agent subnet must have no delegation"):
            preflight.plan([self.config])
        del self.config["platform"]["acr_agent_pool_subnet_id"]
        with self.assertRaisesRegex(ValueError, "Private source build requires.*acr_agent_pool_subnet_id"):
            preflight.plan([self.config])

    def test_shared_build_subnet_get_is_cached_and_pool_settings_are_reported(self):
        self.config["platform"].update(acr_agent_pool_name="classroom-build", acr_agent_pool_instance_count=2)
        other = deepcopy(self.config)
        other.update(environment="training-agent-2", stack_name="agent-2")
        reports = preflight.plan([self.config, other])
        self.assertEqual(reports[0]["build"]["agent_pool"],
                         {"subnet_id": BUILD_SUBNET, "name": "classroom-build", "instance_count": 2})
        self.assertEqual(sum(BUILD_SUBNET + "?" in " ".join(command) for command in self.commands), 1)
        self.assertTrue(any("build-service outbound" in warning for warning in reports[0]["warnings"]))

    def test_build_subnet_must_be_readable_and_in_infrastructure_region(self):
        self.failures[BUILD_SUBNET] = "403 Forbidden"
        with self.assertRaisesRegex(RuntimeError, "ACR build-agent subnet.*unreadable"):
            preflight.plan([self.config])
        self.failures.clear()
        build_vnet = BOUNDARY + "/providers/Microsoft.Network/virtualNetworks/build"
        build_subnet = build_vnet + "/subnets/build"
        self.config["platform"]["acr_agent_pool_subnet_id"] = build_subnet
        self.resources[build_subnet] = {**self.resources[BUILD_SUBNET], "id": build_subnet}
        self.resources[build_vnet] = resource(build_vnet, "Microsoft.Network/virtualNetworks", location="northeurope")
        with self.assertRaisesRegex(ValueError, "ACR build-agent subnet VNet location"):
            preflight.plan([self.config])

    def test_build_pool_settings_are_validated_without_quick_build_fallbacks(self):
        for count in (0, -1, True, 1.5, "1"):
            with self.subTest(count=count):
                self.config["platform"]["acr_agent_pool_instance_count"] = count
                with self.assertRaisesRegex(ValueError, "instance_count must be a positive integer"):
                    preflight.plan([self.config])
        self.config["platform"]["acr_agent_pool_instance_count"] = 1
        self.config["platform"]["acr_agent_pool_name"] = "invalid_pool"
        with self.assertRaisesRegex(ValueError, "acr_agent_pool_name"):
            preflight.plan([self.config])
        self.config["platform"]["acr_agent_pool_name"] = "private-build"
        self.config["build"]["mode"] = "quick-build"
        with self.assertRaisesRegex(ValueError, "fallbacks are not permitted"):
            preflight.plan([self.config])

    def test_public_defaults_to_source_without_an_agent_pool(self):
        self.config.update(network_mode="public")
        self.config.pop("build")
        self.config["platform"] = {"existing_resource_group_name": "customer"}
        report, = preflight.plan([self.config])
        self.assertEqual(report["build"], {
            "mode": "source", "agent_pool": None, "allow_trusted_service_import": False,
            "acr_trusted_services_bypass_enabled": False,
        })
        self.assertFalse(any("/subnets/" in " ".join(command) for command in self.commands))
        self.assertFalse(any(SOURCE_REGISTRY in " ".join(command) for command in self.commands))

    def test_private_defaults_to_source_with_dedicated_pool_and_no_registry_assumption(self):
        self.config.pop("build")
        report, = preflight.plan([self.config])
        self.assertEqual(report["build"]["mode"], "source")
        self.assertFalse(report["build"]["allow_trusted_service_import"])
        self.assertFalse(report["build"]["acr_trusted_services_bypass_enabled"])
        self.assertEqual(report["build"]["agent_pool"]["subnet_id"], BUILD_SUBNET)
        self.assertFalse(any(SOURCE_REGISTRY in " ".join(command) for command in self.commands))
        self.assertTrue(any("OCI bases are pulled directly" in warning for warning in report["warnings"]))
        self.assertTrue(any("egress are not verified" in warning for warning in report["warnings"]))
        del self.config["platform"]["acr_agent_pool_subnet_id"]
        with self.assertRaisesRegex(ValueError, "Private source build requires"):
            preflight.plan([self.config])

    def test_import_requires_both_explicit_trusted_service_opt_ins(self):
        for field in ("allow_trusted_service_import", "acr_trusted_services_bypass_enabled"):
            for value in (None, False, "true", 1):
                with self.subTest(field=field, value=value):
                    self.enable_import()
                    settings = self.config["build"] if field == "allow_trusted_service_import" else self.config["platform"]
                    if value is None:
                        settings.pop(field)
                    else:
                        settings[field] = value
                    with self.assertRaisesRegex(ValueError, "opt-in only|must be booleans"):
                        preflight.plan([self.config])
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
