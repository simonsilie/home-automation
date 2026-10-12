"""Offline package regressions; no Home Assistant runtime, secrets, or network.

Run with PyYAML and Jinja2:
    python -m unittest discover -s homeassistant/tests -v
"""

import math
import unittest
from ast import literal_eval
from itertools import product
from pathlib import Path
from typing import Dict

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment


PACKAGES = Path(__file__).resolve().parents[1] / "packages"
OPENDTU = "sensor.opendtu_91fd98_ac_power"
EZ1 = "sensor.ez1_total_power"
INVALID_STATES = ("unknown", "unavailable", "", "not-a-number", "NaN", "nan", "inf", "-inf", "Infinity", "1e309")


def is_number(value: object) -> bool:
    """Match HA's finite-number predicate for entity state strings."""
    try:
        return math.isfinite(float(str(value)))
    except (TypeError, ValueError):
        return False


class SecretLoader(yaml.SafeLoader):
    """Keep secret references symbolic without opening secrets.yaml."""


def secret_reference(loader: SecretLoader, node: yaml.ScalarNode) -> Dict[str, str]:
    return {"secret": loader.construct_scalar(node)}


SecretLoader.add_constructor("!secret", secret_reference)


class SolarPVTests(unittest.TestCase):
    def setUp(self) -> None:
        package = yaml.safe_load((PACKAGES / "solar_pv.yaml").read_text())
        self.sensor = next(
            sensor
            for block in package["template"]
            for sensor in block["sensor"]
            if sensor["unique_id"] == "solar_power_total"
        )
        self.states: Dict[str, str] = {OPENDTU: "100", EZ1: "200"}
        self.environment = NativeEnvironment(undefined=StrictUndefined)
        self.environment.globals.update(
            states=lambda entity: self.states.get(entity, "unknown"),
            is_number=is_number,
        )

    def available(self) -> bool:
        result = self.environment.from_string(self.sensor["availability"]).render()
        if isinstance(result, str):
            self.assertIn(result.strip(), ("True", "False"))
            return result.strip() == "True"
        self.assertIsInstance(result, bool)
        return result

    def state(self) -> object:
        result = self.environment.from_string(self.sensor["state"]).render()
        if isinstance(result, str):
            # HA strips rendered whitespace before parsing a native value.
            try:
                return literal_eval(result.strip())
            except (ValueError, SyntaxError):
                return result.strip()
        return result

    def test_numeric_sources_are_summed_including_zero_at_night(self) -> None:
        for opendtu, ez1, expected in (
            ("123.4", "56.7", 180.1),
            ("100.04", "20.04", 120.1),
            ("0", "0.0", 0.0),
            ("0", "42.5", 42.5),
            ("42.5", "0", 42.5),
            ("1e2", "2e1", 120.0),
            ("-1.5", "10", 8.5),
        ):
            with self.subTest(opendtu=opendtu, ez1=ez1):
                self.states.update({OPENDTU: opendtu, EZ1: ez1})
                self.assertTrue(self.available())
                result = self.state()
                self.assertIsInstance(result, (int, float))
                self.assertEqual(result, expected)

    def test_any_invalid_source_blocks_availability_and_partial_state(self) -> None:
        values = ("0", "123.4") + INVALID_STATES
        for opendtu, ez1 in product(values, repeat=2):
            if opendtu not in INVALID_STATES and ez1 not in INVALID_STATES:
                continue
            with self.subTest(opendtu=opendtu, ez1=ez1):
                self.states.update({OPENDTU: opendtu, EZ1: ez1})
                self.assertFalse(self.available())
                self.assertIsNone(self.state(), "Invalid sources must not produce even a partial or zero total")

    def test_missing_source_blocks_total_and_numeric_recovery_restores_it(self) -> None:
        for entity in (OPENDTU, EZ1):
            with self.subTest(entity=entity):
                self.states.update({OPENDTU: "100", EZ1: "200"})
                del self.states[entity]
                self.assertFalse(self.available())
                self.assertIsNone(self.state())
                self.states[entity] = "0"
                self.assertTrue(self.available())
                self.assertEqual(self.state(), 200 if entity == OPENDTU else 100)

    def test_sensor_identity_and_power_metadata_are_preserved(self) -> None:
        self.assertEqual(self.sensor["name"], "Solar Power Total")
        self.assertEqual(self.sensor["unit_of_measurement"], "W")
        self.assertEqual(self.sensor["device_class"], "power")
        self.assertEqual(self.sensor["state_class"], "measurement")


class WasteCollectionTests(unittest.TestCase):
    def test_source_and_sensor_use_recommended_nested_configuration(self) -> None:
        package = yaml.load((PACKAGES / "waste_collection.yaml").read_text(), Loader=SecretLoader)
        self.assertEqual(set(package), {"waste_collection_schedule"})
        schedule = package["waste_collection_schedule"]
        self.assertEqual(set(schedule), {"sources", "sensors"})
        self.assertEqual(
            schedule["sources"],
            [{"name": "ics", "args": {"url": {"secret": "waste_ics_url"}}}],
        )
        # Upstream SENSOR_CONFIG defaults omitted source_index to 0, the sole ICS source.
        self.assertEqual(schedule["sensors"], [{"name": "muell"}])


if __name__ == "__main__":
    unittest.main()
