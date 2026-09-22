import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from access import ssh_args
from bastion import validate_plan


def plan(*changes):
    return {"resource_changes": [{"address": address, "change": {"actions": actions}}
                                 for address, actions in changes]}


class BastionTests(unittest.TestCase):
    def setUp(self):
        self.allowed = [
            ("azapi_resource.bastion", ["create"]),
            ("azapi_resource.bastion_ip", ["create"]),
            ("azapi_resource.vnet", ["update"]),
        ]

    def test_narrow_plan(self):
        validate_plan(plan(*self.allowed, ("azapi_resource.vm", ["no-op"])))

    def test_replacement_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_plan(plan(*self.allowed, ("azapi_resource.vm", ["delete", "create"])))

    def test_extra_grant_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_plan(plan(*self.allowed, ("azapi_resource.role", ["create"])))

    def test_partial_creation_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_plan(plan(self.allowed[0]))

    def test_tunnel_only_and_host_key_verification(self):
        args = ssh_args()
        self.assertEqual(args[-1], "127.0.0.1")
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("HostKeyAlias=substrate-mvp", args)
        self.assertIn("BatchMode=yes", args)


if __name__ == "__main__":
    unittest.main()
