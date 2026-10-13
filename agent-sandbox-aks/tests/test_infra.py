import copy
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from infra import validate_api_plan, validate_plan
from run_state import CONFIG, accounting, check_limits


def example_plan():
    pools = []
    for name in ("platform", "kata"):
        kata = name == "kata"
        pools.append({
            "name": name, "count": 1,
            "vmSize": CONFIG["azure"]["kata_size" if kata else "platform_size"],
            "enableAutoScaling": False, "enableNodePublicIP": False,
            "securityProfile": {"enableSecureBoot": not kata, "enableVTPM": not kata, "sshAccess": "Disabled"},
            "workloadRuntime": "KataVmIsolation" if kata else "OCIContainer",
            "nodeTaints": ["sandbox=kata:NoSchedule"] if kata else [],
            "upgradeSettings": {"maxSurge": "0", "maxUnavailable": "1"} if kata else {"maxSurge": "1", "maxUnavailable": "0"},
        })
    props = {
        "agentPoolProfiles": pools, "disableLocalAccounts": True,
        "aadProfile": {"enableAzureRBAC": True},
        "networkProfile": {"networkDataplane": "cilium"},
        "apiServerAccessProfile": {"authorizedIPRanges": ["192.0.2.1/32"]},
        "nodeResourceGroup": "test-rg-nodes",
    }
    return {"resource_changes": [
        {"address": "azapi_resource.group", "change": {"actions": ["create"], "after": {
            "name": "test-rg", "parent_id": "/subscriptions/test-sub",
        }}},
        {"address": "azapi_resource.cluster", "change": {"actions": ["create"], "after": {
            "name": "test-aks", "location": CONFIG["azure"]["location"], "body": {"properties": props},
        }}},
    ]}


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = example_plan()
        self.approved = {
            "cluster_name": "test-aks", "operator_cidr": "192.0.2.1/32",
            "resource_group": "test-rg", "subscription_id": "test-sub", "maximum_vcpu": 20,
        }
        self.props = self.plan["resource_changes"][1]["change"]["after"]["body"]["properties"]

    def test_expected_boundary(self):
        validate_plan(self.plan, self.approved)

    def test_no_changes_to_existing_environment(self):
        self.plan["resource_changes"][0]["change"]["actions"] = ["update"]
        with self.assertRaisesRegex(ValueError, "create-only"):
            validate_plan(self.plan, self.approved)

    def test_verified_owned_group_can_remain_unchanged(self):
        self.plan["resource_changes"][0]["change"].update(actions=["no-op"])
        self.plan["resource_changes"][0]["change"]["after"]["id"] = "owned-group"
        validate_plan(self.plan, self.approved, "owned-group")

    def test_no_adoption_of_unverified_group(self):
        self.plan["resource_changes"][0]["change"].update(actions=["no-op"])
        self.plan["resource_changes"][0]["change"]["after"]["id"] = "unrelated-group"
        with self.assertRaisesRegex(ValueError, "create-only"):
            validate_plan(self.plan, self.approved, "owned-group")

    def test_no_subscription_switch(self):
        self.plan["resource_changes"][0]["change"]["after"]["parent_id"] = "/subscriptions/unapproved"
        with self.assertRaisesRegex(ValueError, "scope"):
            validate_plan(self.plan, self.approved)

    def test_no_unapproved_upgrade_capacity(self):
        self.approved["maximum_vcpu"] = 8
        with self.assertRaisesRegex(ValueError, "capacity"):
            validate_plan(self.plan, self.approved)

    def test_no_platform_security_downgrade(self):
        for field in ("enableSecureBoot", "enableVTPM"):
            plan = copy.deepcopy(self.plan)
            plan["resource_changes"][1]["change"]["after"]["body"]["properties"]["agentPoolProfiles"][0]["securityProfile"][field] = False
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Trusted Launch"):
                validate_plan(plan, self.approved)

    def test_no_capacity_growth(self):
        self.props["agentPoolProfiles"][1]["count"] = 2
        with self.assertRaisesRegex(ValueError, "capacity"):
            validate_plan(self.plan, self.approved)

    def test_no_public_node_ssh(self):
        self.props["agentPoolProfiles"][0]["securityProfile"]["sshAccess"] = "LocalUser"
        with self.assertRaisesRegex(ValueError, "SSH"):
            validate_plan(self.plan, self.approved)

    def test_no_api_wildcard(self):
        self.props["apiServerAccessProfile"]["authorizedIPRanges"] = ["0.0.0.0/0"]
        with self.assertRaisesRegex(ValueError, "API"):
            validate_plan(self.plan, self.approved)

    def test_no_ordinary_container_substitute(self):
        self.props["agentPoolProfiles"][1]["workloadRuntime"] = "OCIContainer"
        with self.assertRaisesRegex(ValueError, "runtime"):
            validate_plan(self.plan, self.approved)

    def test_no_missing_taint(self):
        self.props["agentPoolProfiles"][1]["nodeTaints"] = []
        with self.assertRaisesRegex(ValueError, "scheduling"):
            validate_plan(self.plan, self.approved)


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.state = {"deployment_attempts": 0, "repair_cycles": 0, "unknown_operations": 0}

    def test_unknown_operation_blocks_retry(self):
        self.state["unknown_operations"] = 1
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            check_limits(self.state)

    def test_attempt_limit_includes_failed_attempts(self):
        self.state["deployment_attempts"] = CONFIG["limits"]["deployment_attempts"]
        with self.assertRaisesRegex(RuntimeError, "attempts"):
            check_limits(self.state)

    def test_elapsed_time_does_not_reset_on_resume(self):
        at = datetime.now(UTC)
        self.state["cloud_started_at"] = (at - timedelta(hours=4)).isoformat()
        with self.assertRaisesRegex(RuntimeError, "clock"):
            check_limits(self.state, at)

    def test_approved_window_preserves_old_cloud_clock_and_limits_new_execution(self):
        at = datetime.now(UTC)
        self.state["cloud_started_at"] = (at - timedelta(hours=10)).isoformat()
        self.state["execution_window_started_at"] = (at - timedelta(minutes=1)).isoformat()
        check_limits(self.state, at)
        with self.assertRaisesRegex(RuntimeError, "clock"):
            check_limits(self.state, at + timedelta(hours=4))

    def test_allowance_is_not_billed_usage(self):
        at = datetime.now(UTC)
        self.state["cloud_started_at"] = (at - timedelta(hours=1)).isoformat()
        value = accounting(self.state, at)
        self.assertEqual(value["conservative_cloud_usd"], 6)
        self.assertIsNone(value["billed_cloud_usd"])


class ApiRepairTests(unittest.TestCase):
    def setUp(self):
        self.approval = {
            "subscription_id": "sub", "resource_group": "rg", "cluster_name": "aks",
            "operator_cidrs": ["8.8.8.8/32", "8.8.4.4/32"],
        }
        before = {
            "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ContainerService/managedClusters/aks",
            "body": {"properties": {
                "disableLocalAccounts": True,
                "apiServerAccessProfile": {"authorizedIPRanges": ["8.8.8.8/32"]},
            }},
        }
        after = copy.deepcopy(before)
        after["body"]["properties"]["apiServerAccessProfile"]["authorizedIPRanges"] = self.approval["operator_cidrs"]
        self.plan = {"resource_changes": [
            {"address": "azapi_resource.group", "change": {"actions": ["no-op"]}},
            {"address": "azapi_resource.cluster", "change": {"actions": ["update"], "before": before, "after": after}},
        ]}

    def test_additive_operator_repair(self):
        validate_api_plan(self.plan, self.approval)

    def test_unrelated_security_change_rejected(self):
        self.plan["resource_changes"][1]["change"]["after"]["body"]["properties"]["disableLocalAccounts"] = False
        with self.assertRaisesRegex(ValueError, "unrelated"):
            validate_api_plan(self.plan, self.approval)

    def test_cluster_replacement_rejected(self):
        self.plan["resource_changes"][1]["change"]["actions"] = ["delete", "create"]
        with self.assertRaisesRegex(ValueError, "existing"):
            validate_api_plan(self.plan, self.approval)

    def test_broad_range_rejected(self):
        self.approval["operator_cidrs"].append("0.0.0.0/0")
        with self.assertRaisesRegex(ValueError, "public IPv4"):
            validate_api_plan(self.plan, self.approval)


if __name__ == "__main__":
    unittest.main()
