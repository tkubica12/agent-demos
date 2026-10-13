import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qualify import probe_manifest, validate_nodes, validate_vmss


def vmss_examples():
    return [{
        "tags": {"aks-managed-poolName": pool},
        "sku": {"capacity": 1, "name": "Standard_D4s_v3"},
        "virtualMachineProfile": {
            "securityProfile": {
                "securityType": "TrustedLaunch" if pool == "platform" else "Standard",
                "uefiSettings": {"secureBootEnabled": pool == "platform", "vTpmEnabled": pool == "platform"},
            },
            "networkProfile": {"networkInterfaceConfigurations": [{"ipConfigurations": [{}]}]},
        },
    } for pool in ("platform", "kata")]


class QualificationTests(unittest.TestCase):
    def test_exact_native_security_split(self):
        validate_vmss(vmss_examples())

    def test_missing_native_security_is_not_success(self):
        items = vmss_examples()
        del items[0]["virtualMachineProfile"]["securityProfile"]
        with self.assertRaisesRegex(RuntimeError, "Trusted Launch"):
            validate_vmss(items)

    def test_public_node_ip_fails(self):
        items = vmss_examples()
        items[1]["virtualMachineProfile"]["networkProfile"]["networkInterfaceConfigurations"][0]["ipConfigurations"][0]["publicIPAddressConfiguration"] = {"name": "public"}
        with self.assertRaisesRegex(RuntimeError, "public IP"):
            validate_vmss(items)

    def test_duplicate_pool_is_not_two_pools(self):
        with self.assertRaisesRegex(RuntimeError, "identity"):
            validate_vmss([vmss_examples()[0], vmss_examples()[0]])

    def test_benign_probes_are_restricted_and_off_network(self):
        items = probe_manifest()["items"]
        policy = next(x for x in items if x["kind"] == "NetworkPolicy")
        self.assertEqual(policy["spec"]["egress"], [])
        self.assertEqual(policy["spec"]["podSelector"], {})
        for pod in (x for x in items if x["kind"] == "Pod"):
            self.assertFalse(pod["spec"]["automountServiceAccountToken"])
            self.assertTrue(pod["spec"]["securityContext"]["runAsNonRoot"])
            container = pod["spec"]["containers"][0]
            self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
            self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
            self.assertEqual(container["resources"]["limits"]["memory"], "512Mi")

    def test_kata_probe_cannot_fall_back_to_runc(self):
        pod = next(x for x in probe_manifest()["items"] if x["metadata"]["name"] == "kernel-kata")
        self.assertEqual(pod["spec"]["runtimeClassName"], "kata-vm-isolation")
        self.assertEqual(pod["spec"]["nodeSelector"]["kubernetes.azure.com/agentpool"], "kata")

    def test_missing_ready_node_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "two nodes"):
            validate_nodes([])


if __name__ == "__main__":
    unittest.main()
