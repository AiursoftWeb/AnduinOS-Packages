from importlib.machinery import SourceFileLoader
from pathlib import Path
import types
import json
import subprocess
import unittest
from unittest.mock import call, patch
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
loader = SourceFileLoader(
    "network_service_helper", str(ROOT / "scripts/network-service-helper")
)
helper = types.ModuleType(loader.name)
loader.exec_module(helper)


class NetworkServiceHelperTests(unittest.TestCase):
    def test_disable_masks_daemon_and_socket(self):
        with patch.object(helper, "run") as run:
            helper.set_mdns_enabled(False)
        run.assert_called_once_with(
            ["systemctl", "mask", "--now", *helper.MDNS_UNITS]
        )

    def test_enable_unmasks_before_starting_units(self):
        with patch.object(helper, "run") as run:
            helper.set_mdns_enabled(True)
        self.assertEqual(
            run.call_args_list,
            [
                call(["systemctl", "unmask", *helper.MDNS_UNITS]),
                call(["systemctl", "enable", "--now", *helper.MDNS_UNITS]),
            ],
        )

    def test_polkit_action_only_authorizes_the_fixed_helper(self):
        tree = ET.parse(ROOT / "data/com.anduinos.ufwall.policy")
        action = tree.find(
            ".//action[@id='com.anduinos.ufwall.manage-network-discovery']"
        )
        self.assertIsNotNone(action)
        annotations = {
            node.attrib.get("key"): (node.text or "").strip()
            for node in action.findall(".//annotate")
        }
        self.assertEqual(
            annotations["org.freedesktop.policykit.exec.path"],
            "/usr/libexec/ufwall-gtk/network-service-helper",
        )
        self.assertNotIn(
            "org.freedesktop.policykit.exec.allow_gui", annotations
        )



class FirewallHealthTests(unittest.TestCase):
    def collect(self, packages="ufw\tinstalled\n", states=None, nft=None, failed=None):
        states = states or {}
        nft = nft if nft is not None else {"nftables": []}
        def query(command):
            if command[0] == failed:
                raise subprocess.TimeoutExpired(command, 5)
            if command[0] == "dpkg-query":
                return subprocess.CompletedProcess(command, 1, packages, "")
            if command[0] == "systemctl":
                state = states.get(command[2], "LoadState=not-found\nActiveState=inactive\nUnitFileState=\n")
                return subprocess.CompletedProcess(command, 0, state, "")
            return subprocess.CompletedProcess(command, 0, json.dumps(nft), "")
        with patch.object(helper, "query", side_effect=query), \
             patch.object(helper.shutil, "which", return_value="/usr/sbin/nft"), \
             patch.object(helper.os.path, "isfile", return_value="ufw\tinstalled\n" in packages), \
             patch.object(helper.os, "access", return_value=True):
            return helper.firewall_health()

    def test_ufw_and_bottom_level_tools_are_not_conflicts(self):
        report = self.collect(packages="ufw\tinstalled\nnftables\tinstalled\n")
        self.assertTrue(report["ufw_available"])
        self.assertFalse(report["errors"])
        self.assertFalse(any(m["active"] or m["enabled"] or m["rules"] for m in report["managers"]))

    def test_package_only_stays_distinct_from_running_or_enabled(self):
        report = self.collect(packages="ufw\tinstalled\nfirewalld\tinstalled\niptables-persistent\tinstalled\n")
        firewalld, _, persistent = report["managers"]
        self.assertTrue(firewalld["installed"])
        self.assertTrue(persistent["installed"])
        self.assertFalse(firewalld["active"] or firewalld["enabled"] or firewalld["rules"])

    def test_running_and_boot_enabled_services_are_reported_separately(self):
        report = self.collect(states={
            "firewalld.service": "LoadState=loaded\nActiveState=active\nUnitFileState=disabled\n",
            "nftables.service": "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n",
            "netfilter-persistent.service": "LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n",
        })
        self.assertTrue(report["managers"][0]["active"])
        self.assertFalse(report["managers"][0]["enabled"])
        self.assertTrue(report["managers"][1]["enabled"])
        self.assertTrue(report["managers"][2]["active"])

    def test_issue40_firewalld_rules_survive_package_and_service_removal(self):
        report = self.collect(nft={"nftables": [{"chain": {
            "family": "inet", "table": "firewalld", "name": "filter_INPUT",
            "hook": "input", "policy": "accept",
        }}]})
        firewalld = report["managers"][0]
        self.assertFalse(firewalld["installed"] or firewalld["active"])
        self.assertTrue(firewalld["rules"])

    def test_docker_libvirt_and_ufw_rules_are_not_other_firewalls(self):
        chains = [{"chain": {"family": family, "table": table, "name": "INPUT",
                             "hook": "input", "policy": "drop"}}
                  for family, table in [("ip", "filter"), ("ip6", "filter"),
                                        ("inet", "docker-bridges"), ("ip", "libvirt_network")]]
        chains += [{"chain": {"family": family, "table": "filter", "name": name}}
                   for family, name in [("ip", "ufw-before-input"), ("ip6", "ufw6-before-input")]]
        self.assertEqual(set(), helper.rule_owners({"nftables": chains}))

    def test_independent_filtering_detected_but_empty_accept_chain_ignored(self):
        chain = {"family": "inet", "table": "filter", "name": "input", "hook": "input", "policy": "accept"}
        self.assertEqual(set(), helper.rule_owners({"nftables": [{"chain": chain}]}))
        chain["policy"] = "drop"
        self.assertEqual({"nftables"}, helper.rule_owners({"nftables": [{"chain": chain}]}))
        chain["policy"] = "accept"
        rule = {"family": "inet", "table": "filter", "chain": "input", "expr": [{"drop": None}]}
        self.assertEqual({"nftables"}, helper.rule_owners({"nftables": [{"chain": chain}, {"rule": rule}]}))

    def test_unmanaged_ipv4_filter_table_is_detected(self):
        chain = {"family": "ip", "table": "filter", "name": "INPUT", "hook": "input", "policy": "drop"}
        self.assertEqual({"nftables"}, helper.rule_owners({"nftables": [{"chain": chain}]}))

    def test_missing_ufw_is_not_hidden_by_leftover_configuration(self):
        self.assertFalse(self.collect(packages="ufw\tconfig-files\n")["ufw_available"])

    def test_probe_failure_and_failed_service_are_unknown_not_clean(self):
        self.assertIn("rules", self.collect(failed="nft")["errors"])
        self.assertIn("packages", self.collect(failed="dpkg-query")["errors"])
        report = self.collect(states={"firewalld.service": "LoadState=loaded\nActiveState=failed\nUnitFileState=disabled\n"})
        self.assertIn("firewalld", report["errors"])

    def test_inspection_dispatch_has_no_write_operations(self):
        with patch.object(helper.os, "geteuid", return_value=0), \
             patch.object(helper, "firewall_health", return_value={"errors": []}), \
             patch.object(helper, "run") as write, patch("builtins.print"):
            self.assertEqual(0, helper.main(["firewall-health"]))
            write.assert_not_called()
            self.assertEqual(64, helper.main(["firewall-health", "arbitrary-argument"]))

    def test_inspection_polkit_action_is_argument_restricted(self):
        action = ET.parse(ROOT / "data/com.anduinos.ufwall.policy").find(
            ".//action[@id='com.anduinos.ufwall.inspect-firewalls']")
        annotations = {node.attrib["key"]: node.text for node in action.findall("annotate")}
        self.assertEqual("firewall-health", annotations["org.freedesktop.policykit.exec.argv1"])
        self.assertEqual("/usr/libexec/ufwall-gtk/network-service-helper", annotations["org.freedesktop.policykit.exec.path"])


if __name__ == "__main__":
    unittest.main()
