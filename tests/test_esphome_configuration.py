"""Static security and dependency regressions without loading runtime secrets."""

import re
import unittest
from pathlib import Path
from typing import Dict

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ConfigLoader(yaml.SafeLoader):
    pass


def symbolic_tag(loader: ConfigLoader, tag: str, node: yaml.ScalarNode) -> Dict[str, str]:
    return {tag: loader.construct_scalar(node)}


ConfigLoader.add_multi_constructor("!", symbolic_tag)


class ESPHomeConfigurationTests(unittest.TestCase):
    def test_web_access_requires_separate_digest_credentials_and_disables_web_ota(self) -> None:
        config = yaml.load((ROOT / "esphome/common/base.yaml").read_text(), Loader=ConfigLoader)
        self.assertEqual(
            config["web_server"]["auth"],
            {
                "type": "digest",
                "username": {"secret": "web_server_username"},
                "password": {"secret": "web_server_password"},
            },
        )
        self.assertIs(config["web_server"]["ota"], False)
        self.assertEqual(config["wifi"]["ap"]["password"], {"secret": "fallback_ap_password"})
        self.assertEqual(config["ota"][0]["password"], {"secret": "ota_password"})
        examples = yaml.safe_load((ROOT / "esphome/secrets.yaml.example").read_text())
        for key in ("web_server_username", "web_server_password", "fallback_ap_password", "ota_password"):
            self.assertIn(key, examples)
        self.assertEqual(
            len({examples[key] for key in ("web_server_password", "fallback_ap_password", "ota_password")}), 3
        )

    def test_retired_external_components_are_immutable_and_not_continuously_refreshed(self) -> None:
        config = yaml.load((ROOT / "esphome/soyosource-victron-esp32.yaml").read_text(), Loader=ConfigLoader)
        for key in ("external_components_soyosource", "external_components_victron"):
            self.assertRegex(config["substitutions"][key], r"^github://[^@]+@[0-9a-f]{40}$")
        self.assertTrue(all(component["refresh"] == "never" for component in config["external_components"]))

    def test_esp8266_mqtt_does_not_claim_unsupported_tls(self) -> None:
        config = yaml.load((ROOT / "esphome/electric_meter_ir.yaml").read_text(), Loader=ConfigLoader)
        self.assertEqual(config["mqtt"]["port"], 1883)
        self.assertIs(config["mqtt"]["discover_ip"], False)
        self.assertNotIn("certificate_authority", config["mqtt"])

    def test_minimum_firmware_versions_not_newer_than_toolchain(self) -> None:
        project = (ROOT / "pyproject.toml").read_text()
        version = re.search(r'"esphome==([0-9.]+)"', project)
        self.assertIsNotNone(version)
        assert version is not None
        for device in ("electric_meter_ir.yaml", "riden-psu.yaml", "soyosource-victron-esp32.yaml"):
            config = yaml.load((ROOT / "esphome" / device).read_text(), Loader=ConfigLoader)
            self.assertIsInstance(config["esphome"]["min_version"], str)
            self.assertTrue(
                tuple(int(p) for p in config["esphome"]["min_version"].split("."))
                <= tuple(int(p) for p in version.group(1).split(".")),
                f"{device} min_version {config['esphome']['min_version']} is newer than toolchain {version.group(1)}",
            )


if __name__ == "__main__":
    unittest.main()
