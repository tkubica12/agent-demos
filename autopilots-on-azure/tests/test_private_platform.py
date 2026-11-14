from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "terraform" / "platform"
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
CUSTOMER_RG = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/customer"
SUBNET_ROOT = f"{CUSTOMER_RG}/providers/Microsoft.Network/virtualNetworks/customer/subnets"
WORKSPACE = f"{CUSTOMER_RG}/providers/Microsoft.OperationalInsights/workspaces/customer"
APP_INSIGHTS = f"{CUSTOMER_RG}/providers/Microsoft.Insights/components/customer"
AMPLS = f"{CUSTOMER_RG}/providers/Microsoft.Insights/privateLinkScopes/central"
DNS_ZONES = (
    "privatelink.azurecr.io",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.services.ai.azure.com",
    "privatelink.servicebus.windows.net",
    "privatelink.northeurope.azurecontainerapps.io",
)


def hcl_map(values: dict[str, str]) -> str:
    return "{\n" + "\n".join(f'    "{key}" = "{value}"' for key, value in values.items()) + "\n  }"


def dns_ids(parent_zone: bool = False) -> dict[str, str]:
    zones = list(DNS_ZONES)
    if parent_zone:
        zones[-1] = "privatelink.azurecontainerapps.io"
    return {zone: f"{CUSTOMER_RG}/providers/Microsoft.Network/privateDnsZones/{zone}" for zone in zones}


def terraform_tests() -> str:
    mocks = '''
mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned" }
  }
  mock_resource "azurerm_virtual_network" {
    defaults = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.Network/virtualNetworks/owned" }
  }
  mock_resource "azurerm_subnet" {
    defaults = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.Network/virtualNetworks/owned/subnets/owned" }
  }
  mock_resource "azurerm_private_dns_zone" {
    defaults = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.Network/privateDnsZones/owned.example" }
  }
  mock_resource "azurerm_container_registry" {
    defaults = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.ContainerRegistry/registries/owned" }
  }
  mock_resource "azurerm_servicebus_namespace" {
    defaults = { id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.ServiceBus/namespaces/owned" }
  }
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "11111111-1111-1111-1111-111111111111"
      tenant_id       = "22222222-2222-2222-2222-222222222222"
      object_id       = "33333333-3333-3333-3333-333333333333"
    }
  }
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/customer"
      name     = "customer"
      location = "westeurope"
    }
  }
}
mock_provider "random" {
  override_during = plan
  mock_resource "random_string" {
    defaults = { result = "demo" }
  }
}
mock_provider "azapi" {
  override_during = plan
  mock_resource "azapi_resource" {
    defaults = {
      id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.CognitiveServices/accounts/owned"
      output = {
        properties = {
          AppId = "application-id"
          ConnectionString = "InstrumentationKey=test"
          defaultDomain = "private.northeurope.azurecontainerapps.io"
          publicNetworkAccess = "Disabled"
        }
        identity = { principalId = "44444444-4444-4444-4444-444444444444" }
      }
    }
  }
  mock_data "azapi_resource" {
    defaults = {
      output = {
        properties = {
          AppId = "existing-application-id"
          ConnectionString = "InstrumentationKey=customer"
          WorkspaceResourceId = "WORKSPACE_ID"
          DisableLocalAuth = true
          features = { disableLocalAuth = true }
          publicNetworkAccessForIngestion = "Disabled"
          publicNetworkAccessForQuery = "Disabled"
        }
      }
    }
  }
}
override_resource {
  target = azapi_resource.foundry_project
  override_during = plan
  values = {
    id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.CognitiveServices/accounts/owned/projects/autopilots-project"
    output = { identity = { principalId = "44444444-4444-4444-4444-444444444444" } }
  }
}
override_resource {
  target = azapi_resource.foundry_private_endpoint
  override_during = plan
  values = {
    id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/owned/providers/Microsoft.Network/privateEndpoints/pe-foundry-demo"
  }
}
variables {
  location         = "swedencentral"
  sandbox_location = "northeurope"
  apps_location    = "westeurope"
  environment_name = "training"
}
'''.replace("WORKSPACE_ID", WORKSPACE)

    def run(name: str, variables: str, assertions: list[str], failure: str = "", overrides: str = "") -> str:
        body = f'\nrun "{name}" {{\n  command = plan\n'
        if variables:
            body += f"  variables {{\n{variables}\n  }}\n"
        if failure:
            body += f"  expect_failures = [{failure}]\n"
        body += overrides
        for index, condition in enumerate(assertions):
            body += (
                "  assert {\n"
                f"    condition = {condition}\n"
                f'    error_message = "{name} contract {index + 1} failed."\n'
                "  }\n"
            )
        return body + "}\n"

    public = run("public_defaults_unchanged", "", [
        'azurerm_container_registry.main[0].sku == "Basic" && azurerm_container_registry.main[0].public_network_access_enabled && !azurerm_container_registry.main[0].admin_enabled',
        'azurerm_container_registry.main[0].network_rule_bypass_option == "AzureServices" && output.acr_trusted_services_bypass_enabled && output.acr_network_rule_bypass_option == "AzureServices"',
        'length(azurerm_servicebus_namespace.scheduler) == 1 && azurerm_servicebus_namespace.scheduler[0].sku == "Standard" && azurerm_servicebus_namespace.scheduler[0].public_network_access_enabled && !azurerm_servicebus_namespace.scheduler[0].local_auth_enabled',
        'azapi_resource.foundry[0].body.properties.publicNetworkAccess == "Enabled" && azapi_resource.foundry[0].body.properties.disableLocalAuth',
        'azapi_resource.log_analytics[0].body.properties.publicNetworkAccessForIngestion == "Enabled" && azapi_resource.application_insights[0].body.properties.publicNetworkAccessForQuery == "Enabled"',
        'azurerm_virtual_network.main[0].location == "westeurope" && azurerm_private_endpoint.private_mcp.location == "westeurope" && azurerm_virtual_network.sandbox[0].location == "northeurope"',
        "length(azurerm_private_endpoint.services) == 0 && length(azurerm_private_dns_zone.services) == 0",
        "length(azapi_resource.foundry_private_endpoint) == 0 && length(azapi_resource.foundry_private_dns_zone_group) == 0",
        'length(azurerm_resource_group.main) == 1 && length(azurerm_virtual_network_peering.sandbox_to_private_mcp) == 1 && length(azurerm_private_dns_zone.private_mcp) == 1',
        'output.location == "westeurope" && output.sandbox_location == "northeurope"',
        'output.acr_agent_pool_name == "" && length(azurerm_container_registry_agent_pool.private) == 0',
    ])
    private = run("private_demo_network_and_monitoring", '''
    private_network_enabled = true
    required_tags = {
      CostCenter = "TRAINING"
      app = "cannot-replace"
      layer = "cannot-replace"
      environment = "cannot-replace"
    }
''', [
        'azurerm_container_registry.main[0].sku == "Premium" && !azurerm_container_registry.main[0].public_network_access_enabled && !azurerm_container_registry.main[0].admin_enabled',
        'azurerm_container_registry.main[0].network_rule_bypass_option == "None" && !output.acr_trusted_services_bypass_enabled && output.acr_network_rule_bypass_option == "None"',
        'length(azurerm_servicebus_namespace.scheduler) == 1 && azurerm_servicebus_namespace.scheduler[0].sku == "Premium" && azurerm_servicebus_namespace.scheduler[0].capacity == 1 && !azurerm_servicebus_namespace.scheduler[0].public_network_access_enabled && !azurerm_servicebus_namespace.scheduler[0].local_auth_enabled',
        'azapi_resource.foundry[0].body.properties.publicNetworkAccess == "Disabled" && azapi_resource.foundry[0].body.properties.disableLocalAuth && azapi_resource.foundry[0].location == "swedencentral"',
        'azapi_resource.foundry_project.location == "swedencentral" && azurerm_resource_group.main[0].location == "northeurope"',
        'azurerm_container_registry.main[0].location == "northeurope" && azurerm_servicebus_namespace.scheduler[0].location == "northeurope" && azurerm_private_endpoint.private_mcp.location == "northeurope"',
        'alltrue([for endpoint in azurerm_private_endpoint.services : endpoint.location == "northeurope"]) && azurerm_virtual_network.main[0].location == "northeurope"',
        'length(azurerm_private_endpoint.services) == 2 && !contains(keys(azurerm_private_endpoint.services), "foundry") && length(azapi_resource.foundry_private_endpoint) == 1 && length(azurerm_private_dns_zone.services) == 5 && length(azurerm_private_dns_zone.private_mcp) == 1',
        'azapi_resource.foundry_private_endpoint[0].name == "pe-foundry-demo" && azapi_resource.foundry_private_endpoint[0].location == "northeurope" && azapi_resource.foundry_private_endpoint[0].body.properties.privateLinkServiceConnections[0].properties.privateLinkServiceId == azapi_resource.foundry[0].id && azapi_resource.foundry_private_endpoint[0].body.properties.privateLinkServiceConnections[0].properties.groupIds == ["account"]',
        'length(azapi_resource.foundry_private_dns_zone_group[0].body.properties.privateDnsZoneConfigs) == 3 && azapi_resource.foundry_private_dns_zone_group[0].name == "foundry" && azapi_resource.foundry_private_dns_zone_group[0].parent_id == azapi_resource.foundry_private_endpoint[0].id',
        'output.private_endpoint_ids.foundry == azapi_resource.foundry_private_endpoint[0].id',
        'azapi_resource.private_mcp_env.body.properties.environmentMode == "Express" && azapi_resource.private_mcp_env.body.properties.publicNetworkAccess == "Disabled"',
        'azapi_resource.log_analytics[0].body.properties.publicNetworkAccessForIngestion == "Disabled" && azapi_resource.log_analytics[0].body.properties.publicNetworkAccessForQuery == "Disabled" && azapi_resource.log_analytics[0].body.properties.features.disableLocalAuth',
        'azapi_resource.application_insights[0].body.properties.publicNetworkAccessForIngestion == "Disabled" && azapi_resource.application_insights[0].body.properties.publicNetworkAccessForQuery == "Disabled" && azapi_resource.application_insights[0].body.properties.DisableLocalAuth',
        'azurerm_container_registry.main[0].tags.CostCenter == "TRAINING" && azurerm_container_registry.main[0].tags.app == "autopilots-on-azure" && azurerm_container_registry.main[0].tags.layer == "platform" && azurerm_container_registry.main[0].tags.environment == "training"',
        'output.monitoring_private_link_handoff.ampls_handoff_required && length(azapi_resource.monitor_scoped_resource) == 0',
        'output.location == "northeurope" && output.ai_location == "swedencentral"',
    ])
    external_inputs = f'''
    private_network_enabled = true
    existing_resource_group_name = "customer"
    existing_sandbox_subnet_id = "{SUBNET_ROOT}/sandbox"
    existing_private_endpoint_subnet_id = "{SUBNET_ROOT}/endpoints"
    private_dns_zone_ids = {hcl_map(dns_ids())}
'''
    existing = run("existing_network_owns_no_customer_infrastructure", external_inputs, [
        "length(azurerm_resource_group.main) == 0 && length(azurerm_virtual_network.main) == 0 && length(azurerm_virtual_network.sandbox) == 0",
        "length(azurerm_subnet.sandbox) == 0 && length(azurerm_subnet.private_endpoints) == 0",
        "length(azurerm_virtual_network_peering.sandbox_to_private_mcp) == 0 && length(azurerm_virtual_network_peering.private_mcp_to_sandbox) == 0",
        "length(azurerm_private_dns_zone.private_mcp) == 0 && length(azurerm_private_dns_zone.services) == 0",
        "length(azurerm_private_dns_zone_virtual_network_link.private_mcp) == 0 && length(azurerm_private_dns_zone_virtual_network_link.sandbox) == 0 && length(azurerm_private_dns_zone_virtual_network_link.services_main) == 0 && length(azurerm_private_dns_zone_virtual_network_link.services_sandbox) == 0",
        f'output.sandbox_subnet_id == "{SUBNET_ROOT}/sandbox" && azurerm_private_endpoint.private_mcp.subnet_id == "{SUBNET_ROOT}/endpoints"',
        'output.resource_group_name == "customer" && azapi_resource.foundry[0].location == "swedencentral"',
        f'azurerm_private_endpoint.services["acr"].private_dns_zone_group[0].private_dns_zone_ids == tolist(["{dns_ids()["privatelink.azurecr.io"]}"])',
        f'azapi_resource.foundry_private_endpoint[0].body.properties.subnet.id == "{SUBNET_ROOT}/endpoints"',
        'toset([for zone in azapi_resource.foundry_private_dns_zone_group[0].body.properties.privateDnsZoneConfigs : zone.properties.privateDnsZoneId]) == toset([for zone in ["privatelink.cognitiveservices.azure.com", "privatelink.openai.azure.com", "privatelink.services.ai.azure.com"] : output.private_dns_zone_ids[zone]])',
    ])
    no_scheduler_assertions = [
        "length(azurerm_servicebus_namespace.scheduler) == 0",
        '!contains(keys(azurerm_private_endpoint.services), "scheduler") && length(azurerm_private_endpoint.services) == 1 && length(azapi_resource.foundry_private_endpoint) == 1',
        '!contains(keys(output.private_dns_zone_ids), "privatelink.servicebus.windows.net") && !contains(output.private_network_handoff.required_private_dns_zones, "privatelink.servicebus.windows.net")',
        'output.scheduler_servicebus_namespace_id == "" && output.scheduler_servicebus_namespace_name == "" && output.scheduler_servicebus_fully_qualified_namespace == ""',
    ]
    no_scheduler = (
        run("private_simulator_owns_no_scheduler_resources", '''
    private_network_enabled = true
    scheduler_enabled = false
''', no_scheduler_assertions + [
            'length(azurerm_private_dns_zone.services) == 4 && !contains(keys(azurerm_private_dns_zone.services), "privatelink.servicebus.windows.net")',
            'length(azurerm_private_dns_zone_virtual_network_link.services_main) == 4 && length(azurerm_private_dns_zone_virtual_network_link.services_sandbox) == 4',
        ])
        + run("private_simulator_requires_no_central_servicebus_dns", external_inputs.replace(
            hcl_map(dns_ids()), hcl_map({zone: id for zone, id in dns_ids().items() if zone != "privatelink.servicebus.windows.net"}),
        ) + "    scheduler_enabled = false\n", no_scheduler_assertions)
        + run("private_simulator_ignores_unused_central_servicebus_dns",
            external_inputs + "    scheduler_enabled = false\n", no_scheduler_assertions)
        + run("prebuilt_simulator_has_no_registry_or_registry_dns", external_inputs.replace(
            hcl_map(dns_ids()), hcl_map({zone: id for zone, id in dns_ids().items()
                                       if zone not in {"privatelink.azurecr.io", "privatelink.servicebus.windows.net"}}),
        ) + "    scheduler_enabled = false\n    registry_enabled = false\n", [
            "length(azurerm_container_registry.main) == 0 && length(azurerm_container_registry_agent_pool.private) == 0",
            'output.acr_id == "" && output.acr_name == "" && output.acr_login_server == ""',
            'length(azurerm_private_endpoint.services) == 0 && length(azapi_resource.foundry_private_endpoint) == 1',
            '!contains(keys(output.private_dns_zone_ids), "privatelink.azurecr.io") && !contains(output.private_network_handoff.required_private_dns_zones, "privatelink.azurecr.io")',
        ])
    )
    independent_rg = run("existing_rg_with_owned_network", '''
    private_network_enabled = true
    existing_resource_group_name = "customer"
''', [
        'length(azurerm_resource_group.main) == 0 && length(azurerm_virtual_network.main) == 1 && azurerm_virtual_network.main[0].resource_group_name == "customer"',
    ])
    independent_subnets = run("owned_rg_with_existing_network_and_parent_dns",
        external_inputs.replace('existing_resource_group_name = "customer"', "").replace(
            hcl_map(dns_ids()), hcl_map(dns_ids(parent_zone=True)),
        ), [
            "length(azurerm_resource_group.main) == 1 && length(azurerm_virtual_network.main) == 0 && length(azurerm_private_dns_zone.private_mcp) == 0",
            f'azurerm_private_endpoint.private_mcp.private_dns_zone_group[0].private_dns_zone_ids == tolist(["{dns_ids(parent_zone=True)["privatelink.azurecontainerapps.io"]}"])',
        ])
    monitoring_inputs = f'''
    existing_log_analytics_workspace_id = "{WORKSPACE}"
    existing_application_insights_id = "{APP_INSIGHTS}"
    ampls_id = "{AMPLS}"
'''
    central_monitoring = run("existing_monitoring_is_read_only", external_inputs + monitoring_inputs, [
        "length(azapi_resource.log_analytics) == 0 && length(azapi_resource.application_insights) == 0 && length(azapi_resource.monitor_scoped_resource) == 0",
        f'output.log_analytics_workspace_id == "{WORKSPACE}" && output.application_insights_id == "{APP_INSIGHTS}"',
        'output.application_insights_app_id == "existing-application-id" && nonsensitive(output.application_insights_connection_string) == "InstrumentationKey=customer"',
        f'azurerm_role_assignment.project_trace_reader["workspace"].scope == "{WORKSPACE}" && azurerm_role_assignment.operator_trace_reader["application-insights"].scope == "{APP_INSIGHTS}"',
        "output.monitoring_private_link_handoff.ampls_handoff_required",
    ])
    ampls = run("owned_monitoring_links_to_central_ampls", f'''
    private_network_enabled = true
    ampls_id = "{AMPLS}"
''', [
        "length(azapi_resource.monitor_scoped_resource) == 2 && !output.monitoring_private_link_handoff.ampls_handoff_required",
        f'alltrue([for link in azapi_resource.monitor_scoped_resource : link.parent_id == "{AMPLS}"])',
    ])
    agent_pool = run("private_build_pool_uses_dedicated_existing_subnet", external_inputs + f'''
    acr_agent_pool_subnet_id = "{SUBNET_ROOT}/build-agents"
    acr_agent_pool_instance_count = 2
''', [
        'length(azurerm_container_registry_agent_pool.private) == 1 && output.acr_agent_pool_name == "private-build"',
        'azurerm_container_registry_agent_pool.private[0].tier == "S1" && azurerm_container_registry_agent_pool.private[0].instance_count == 2 && azurerm_container_registry_agent_pool.private[0].location == "northeurope"',
        f'azurerm_container_registry_agent_pool.private[0].virtual_network_subnet_id == "{SUBNET_ROOT}/build-agents" && length(azurerm_subnet.sandbox) == 0 && length(azurerm_subnet.private_endpoints) == 0',
        'azurerm_container_registry.main[0].network_rule_bypass_option == "None" && !output.acr_trusted_services_bypass_enabled',
    ])
    approved_bypass = run("private_trusted_services_bypass_requires_explicit_opt_in", '''
    private_network_enabled = true
    scheduler_enabled = false
    acr_trusted_services_bypass_enabled = true
''', [
        'azurerm_container_registry.main[0].network_rule_bypass_option == "AzureServices" && output.acr_trusted_services_bypass_enabled && output.acr_network_rule_bypass_option == "AzureServices"',
        '!azurerm_container_registry.main[0].public_network_access_enabled && !azurerm_container_registry.main[0].admin_enabled',
    ])
    existing_monitoring_override = f'''
  override_data {{
    target = data.azapi_resource.existing_application_insights[0]
    values = {{
      output = {{
        properties = {{
          AppId = "existing-application-id"
          ConnectionString = "InstrumentationKey=customer"
          WorkspaceResourceId = "{WORKSPACE}"
          DisableLocalAuth = true
          publicNetworkAccessForIngestion = "Disabled"
          publicNetworkAccessForQuery = "Disabled"
        }}
      }}
    }}
  }}
'''
    failures = "".join([
        run("reject_partial_subnets", f'''
    private_network_enabled = true
    existing_sandbox_subnet_id = "{SUBNET_ROOT}/sandbox"
''', [], "azapi_resource.private_mcp_env"),
        run("reject_public_existing_subnets", external_inputs.replace(
            "private_network_enabled = true", "private_network_enabled = false"
        ), [], "azapi_resource.private_mcp_env"),
        run("reject_missing_central_dns", external_inputs.replace(
            f"private_dns_zone_ids = {hcl_map(dns_ids())}", "private_dns_zone_ids = {}"
        ), [], "azapi_resource.private_mcp_env"),
        run("reject_prebuilt_missing_central_dns", external_inputs.replace(
            f"private_dns_zone_ids = {hcl_map(dns_ids())}", "private_dns_zone_ids = {}"
        ) + "    registry_enabled = false\n    scheduler_enabled = false\n", [], "azapi_resource.private_mcp_env"),
        run("reject_partial_monitoring", f'''
    existing_log_analytics_workspace_id = "{WORKSPACE}"
''', [], "azapi_resource.private_mcp_env"),
        run("reject_shared_subnet", external_inputs.replace(
            f"{SUBNET_ROOT}/endpoints", f"{SUBNET_ROOT}/sandbox"
        ), [], "azapi_resource.private_mcp_env"),
        run("reject_public_ampls", f'    ampls_id = "{AMPLS}"', [], "azapi_resource.private_mcp_env"),
        run("reject_mismatched_dns_id", external_inputs.replace(
            dns_ids()["privatelink.azurecr.io"],
            f"{CUSTOMER_RG}/providers/Microsoft.Network/privateDnsZones/wrong.example",
        ), [], "azapi_resource.private_mcp_env"),
        run("reject_agent_pool_in_sandbox_subnet", external_inputs + f'''
    acr_agent_pool_subnet_id = "{SUBNET_ROOT}/sandbox"
''', [], "azapi_resource.private_mcp_env"),
        run("reject_agent_pool_in_public_mode", f'''
    acr_agent_pool_subnet_id = "{SUBNET_ROOT}/build-agents"
''', [], "azapi_resource.private_mcp_env"),
        run("reject_mismatched_existing_monitoring_workspace", external_inputs + monitoring_inputs,
            [], "data.azapi_resource.existing_application_insights[0]",
            existing_monitoring_override.replace(WORKSPACE, WORKSPACE + "-other")),
        run("reject_existing_public_monitoring", external_inputs + monitoring_inputs,
            [], "data.azapi_resource.existing_application_insights[0]",
            existing_monitoring_override.replace(
                'publicNetworkAccessForIngestion = "Disabled"', 'publicNetworkAccessForIngestion = "Enabled"',
            )),
    ])
    shared = run("shared_foundry_owns_only_stack_project", external_inputs + '''
    environment_name = "training-agent-dev"
    registry_enabled = false
    scheduler_enabled = false
    shared_foundry_state_path = "central.tfstate"
''', [
        'length(azapi_resource.foundry) == 0 && length(azapi_resource.foundry_deployment) == 0',
        'length(azapi_resource.foundry_private_endpoint) == 0 && length(azapi_resource.foundry_private_dns_zone_group) == 0',
        f'azapi_resource.foundry_project.parent_id == "{CUSTOMER_RG}/providers/Microsoft.CognitiveServices/accounts/shared"',
        'azapi_resource.foundry_project.name == "training-agent-dev"',
        'output.foundry_openai_base_url == "https://shared.services.ai.azure.com/openai/v1"',
        'output.foundry_project_endpoint == "https://shared.services.ai.azure.com/api/projects/training-agent-dev"',
        'output.model_deployment_name == "pooled" && output.ai_location == "swedencentral"',
    ], overrides=f'''
  override_data {{
    target = data.terraform_remote_state.shared_foundry[0]
    values = {{
      outputs = {{
        foundry_id = "{CUSTOMER_RG}/providers/Microsoft.CognitiveServices/accounts/shared"
        foundry_name = "shared"
        foundry_subdomain = "shared"
        model_deployment_name = "pooled"
        ai_location = "swedencentral"
      }}
    }}
  }}
''')
    return mocks + public + private + existing + no_scheduler + independent_rg + independent_subnets + central_monitoring + ampls + agent_pool + approved_bypass + failures + shared


@unittest.skipUnless(shutil.which("terraform"), "Terraform is required for offline private platform contract tests")
class PrivatePlatformTests(unittest.TestCase):
    def test_isolated_validate_and_private_public_plan_contracts(self):
        self.validate_root(PLATFORM, terraform_tests())

    def test_shared_foundry_create_and_existing_lifecycle_contracts(self):
        mocks = terraform_tests().split("override_resource {", 1)[0]
        self.validate_root(ROOT / "terraform" / "shared-foundry", mocks + f'''
variables {{
  environment_name = "training"
  mode = "create"
  account_name = "shared"
  existing_resource_group_name = "customer"
  private_network_enabled = true
  sandbox_location = "northeurope"
  existing_private_endpoint_subnet_id = "{SUBNET_ROOT}/endpoints"
  private_dns_zone_ids = {hcl_map(dns_ids())}
}}
run "create_one_private_pool" {{
  command = plan
  assert {{
    condition = length(azapi_resource.foundry) == 1 && length(azapi_resource.foundry_deployment) == 1 && length(azapi_resource.foundry_private_endpoint) == 1
    error_message = "One central account, model and PE are required."
  }}
  assert {{
    condition = azapi_resource.foundry[0].body.properties.publicNetworkAccess == "Disabled" && azapi_resource.foundry[0].body.properties.disableLocalAuth
    error_message = "Central account must be private and keyless."
  }}
  assert {{
    condition = azapi_resource.foundry_deployment[0].body.sku.capacity == 15 && length(azapi_resource.foundry_private_dns_zone_group[0].body.properties.privateDnsZoneConfigs) == 3
    error_message = "Pooled capacity and all three DNS zones are required."
  }}
}}
run "existing_account_is_read_only" {{
  command = plan
  variables {{
    mode = "existing"
    account_resource_id = "{CUSTOMER_RG}/providers/Microsoft.CognitiveServices/accounts/shared"
  }}
  override_data {{
    target = data.azapi_resource.foundry[0]
    values = {{
      name = "shared"
      location = "swedencentral"
      output = {{ properties = {{
        customSubDomainName = "shared-domain"
        allowProjectManagement = true
        disableLocalAuth = true
        publicNetworkAccess = "Disabled"
      }} }}
    }}
  }}
  override_data {{
    target = data.azapi_resource.deployment[0]
    values = {{ output = {{ properties = {{ provisioningState = "Succeeded" }} }} }}
  }}
  assert {{
    condition = length(azapi_resource.foundry) == 0 && length(azapi_resource.foundry_deployment) == 0 && length(azapi_resource.foundry_private_endpoint) == 0 && length(azapi_resource.foundry_private_dns_zone_group) == 0
    error_message = "External Foundry lifecycle must not be owned."
  }}
  assert {{
    condition = output.foundry_openai_base_url == "https://shared-domain.services.ai.azure.com/openai/v1"
    error_message = "Use actual account custom subdomain, not the resource name."
  }}
}}
''')

    def test_simulator_runtime_uses_shared_scope_and_unique_agent_name(self):
        account_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/central/providers/Microsoft.CognitiveServices/accounts/shared"
        self.validate_root(ROOT / "terraform" / "apps", f'''
mock_provider "azapi" {{
  override_during = plan
  mock_resource "azapi_resource" {{
    defaults = {{
      id = "{CUSTOMER_RG}/providers/Microsoft.App/sandboxGroups/demo"
      output = {{ properties = {{ publicNetworkAccess = "Disabled" }} }}
    }}
  }}
}}
mock_provider "azurerm" {{
  override_during = plan
  mock_data "azurerm_client_config" {{
    defaults = {{
      subscription_id = "{SUBSCRIPTION}"
      tenant_id = "22222222-2222-2222-2222-222222222222"
      object_id = "33333333-3333-3333-3333-333333333333"
    }}
  }}
}}
override_data {{
  target = data.terraform_remote_state.platform
  values = {{
    outputs = {{
      suffix = "demo"
      resource_group_name = "customer"
      sandbox_location = "northeurope"
      sandbox_subnet_id = "{SUBNET_ROOT}/sandbox"
      foundry_id = "{account_id}"
      foundry_project_endpoint = "https://shared.services.ai.azure.com/api/projects/training-agent-dev"
      acr_id = ""
      application_insights_id = "{APP_INSIGHTS}"
      private_mcp_env_id = "{CUSTOMER_RG}/providers/Microsoft.App/managedEnvironments/demo"
      private_mcp_default_domain = "private.example"
    }}
  }}
}}
variables {{
  autopilot_name = "agent-dev"
  channel = "simulator"
  platform_state_path = "stack.tfstate"
  deployer_principal_id = "33333333-3333-3333-3333-333333333333"
  private_network_enabled = true
}}
run "shared_runtime_role_and_agent_dev" {{
  command = plan
  assert {{
    condition = azurerm_role_assignment.runtime_foundry_user.scope == "{account_id}" && azurerm_role_assignment.runtime_foundry_user.role_definition_name == "Cognitive Services OpenAI User"
    error_message = "Runtime role must target the shared account."
  }}
  assert {{
    condition = output.foundry_agent_name == "autopilots-hermes-agent-dev" && azapi_resource.sandbox_group["runtime"].name == "ap-agent-dev-runtime-demo"
    error_message = "Dev identity, agent registration and native resource names must remain unique."
  }}
}}
run "another_stack_has_distinct_agent_name" {{
  command = plan
  variables {{ autopilot_name = "agent-1" }}
  assert {{
    condition = output.foundry_agent_name == "autopilots-hermes-agent-1"
    error_message = "A second stack must not reuse the dev agent name."
  }}
}}
''')

    def validate_root(self, source_root: Path, tests: str):
        cache = Path(os.environ.get("TF_PLUGIN_CACHE_DIR", ROOT / ".local" / "terraform-provider-cache"))
        if not cache.is_dir():
            self.skipTest("Provide an existing provider mirror with TF_PLUGIN_CACHE_DIR; tests never download providers.")
        isolated = ROOT / ".local" / f"private-platform-validation-{uuid.uuid4().hex}"
        isolated.mkdir(parents=True)
        try:
            for source in [*source_root.glob("*.tf"), source_root / ".terraform.lock.hcl"]:
                shutil.copy2(source, isolated / source.name)
            (isolated / "private_platform.tftest.hcl").write_text(tests, encoding="utf-8")
            environment = {
                key: value for key, value in os.environ.items()
                if key != "TF_DATA_DIR" and not key.startswith("TF_CLI_ARGS")
            }
            environment.update(TF_IN_AUTOMATION="1", CHECKPOINT_DISABLE="1")
            for command in (
                ["init", "-backend=false", "-input=false", "-lockfile=readonly", f"-plugin-dir={cache.resolve()}"],
                ["validate"],
                ["test"],
            ):
                with self.subTest(terraform=command[0]):
                    result = subprocess.run(
                        ["terraform", f"-chdir={isolated}", *command, "-no-color"],
                        text=True, capture_output=True, env=environment, timeout=180,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if command[0] in {"validate", "test"}:
                        print(result.stdout.strip())
            self.assertEqual(
                (source_root / ".terraform.lock.hcl").read_bytes(),
                (isolated / ".terraform.lock.hcl").read_bytes(),
            )
        finally:
            shutil.rmtree(isolated)

    def test_all_new_count_addresses_have_moves_and_model_order_is_preserved(self):
        sources = "\n".join(source.read_text() for source in PLATFORM.glob("*.tf"))
        for address in (
            "azurerm_resource_group.main",
            "azurerm_servicebus_namespace.scheduler",
            "azurerm_virtual_network.main",
            "azurerm_virtual_network.sandbox",
            "azurerm_subnet.sandbox",
            "azurerm_subnet.private_endpoints",
            "azurerm_private_dns_zone.private_mcp",
            "azurerm_private_dns_zone_virtual_network_link.private_mcp",
            "azurerm_private_dns_zone_virtual_network_link.sandbox",
            "azurerm_virtual_network_peering.sandbox_to_private_mcp",
            "azurerm_virtual_network_peering.private_mcp_to_sandbox",
            "azapi_resource.log_analytics",
            "azapi_resource.application_insights",
        ):
            with self.subTest(address=address):
                self.assertIn(f"from = {address}\n", sources)
                self.assertIn(f"to   = {address}[0]\n", sources)
        self.assertIn("depends_on = [azapi_resource.foundry_deployment]", sources)

    def test_foundry_private_endpoint_retries_only_known_account_state_after_setup(self):
        sources = (PLATFORM / "networking.tf").read_text()
        endpoint = sources.split('resource "azapi_resource" "foundry_private_endpoint" {', 1)[1].split(
            'resource "azapi_resource" "foundry_private_dns_zone_group" {', 1
        )[0]
        self.assertIn('error_message_regex  = ["AccountProvisioningStateInvalid"]', endpoint)
        dependencies = endpoint.split("depends_on = [", 1)[1].split("]", 1)[0]
        for resource in (
            "azapi_resource.foundry_deployment",
            "azapi_resource.foundry_project",
            "azapi_resource.foundry_observability_connection",
        ):
            self.assertIn(resource, dependencies)


if __name__ == "__main__":
    unittest.main()
