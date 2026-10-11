"""Offline YAML/template regressions; not a Home Assistant runtime simulation.

Run with an environment containing PyYAML and Jinja2:
    python -m unittest discover -s homeassistant/tests -v
"""

import math
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment


PACKAGE = Path(__file__).resolve().parents[1] / "packages" / "ir_heizung_kinderzimmer2_control.yaml"
CLIMATE = "climate.ir_heizung_kinderzimmer_2"
ENABLED = "input_boolean.ir_kz2_surplus_control_enabled"
POWER = "input_number.ir_kz2_heater_power"
GRID = "sensor.grid_power_average"
SOC = "sensor.gx_device_dc_batterieladung"
BATTERY = "sensor.gx_device_dc_batterieleistung"
TEMPERATURE = "sensor.0xe406bffffedb6d41_temperature"
READY = "binary_sensor.ir_kz2_surplus_ready"
STOP = "binary_sensor.ir_kz2_surplus_stop"


def is_number(value: object) -> bool:
    try:
        return math.isfinite(float(str(value)))
    except (TypeError, ValueError):
        return False


class HeaterControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = yaml.safe_load(PACKAGE.read_text())
        self.sensors = {
            sensor["unique_id"]: sensor for block in self.package["template"] for sensor in block["binary_sensor"]
        }
        self.states: Dict[str, str] = {
            ENABLED: "on",
            POWER: "600",
            GRID: "-1000",
            SOC: "99",
            BATTERY: "0",
            TEMPERATURE: "20",
            CLIMATE: "off",
            READY: "off",
            STOP: "off",
        }
        self.environment = NativeEnvironment(undefined=StrictUndefined)
        self.environment.globals.update(
            states=lambda entity: self.states.get(entity, "unknown"),
            is_state=lambda entity, state: self.states.get(entity, "unknown") == state,
            is_number=is_number,
        )

    def render(self, template: str, trigger_id: str = "periodic") -> bool:
        result = self.environment.from_string(template).render(trigger={"id": trigger_id})
        if isinstance(result, str):
            self.assertIn(result.strip(), ("True", "False"))
            return result.strip() == "True"
        self.assertIsInstance(result, bool)
        return result

    def ready(self) -> bool:
        return self.render(self.sensors["ir_kz2_surplus_ready"]["state"])

    def stop(self) -> bool:
        return self.render(self.sensors["ir_kz2_surplus_stop"]["state"])

    def decision(self, trigger_id: str = "periodic") -> Optional[Dict[str, Any]]:
        branches = self.package["automation"][0]["action"][0]["choose"]
        matches = [branch for branch in branches if self.render(branch["conditions"], trigger_id)]
        self.assertLessEqual(len(matches), 1, "Control branches must not compete")
        return matches[0]["sequence"][0] if matches else None

    def required_action(self, trigger_id: str = "periodic") -> Dict[str, Any]:
        action = self.decision(trigger_id)
        self.assertIsNotNone(action)
        assert action is not None
        return action

    def test_defaults_require_opt_in_and_heater_power_configuration(self) -> None:
        self.assertIs(self.package["input_boolean"]["ir_kz2_surplus_control_enabled"]["initial"], False)
        power = self.package["input_number"]["ir_kz2_heater_power"]
        self.assertEqual(power["min"], 0)
        self.assertNotIn("initial", power, "Configured wattage must survive restarts")
        thermostats = yaml.safe_load((PACKAGE.parents[1] / "climate.yaml").read_text())
        nursery = next(item for item in thermostats if item["unique_id"] == "kz2_heizung")
        self.assertEqual(nursery["initial_hvac_mode"], "off")

    def test_export_must_cover_heater_plus_reserve_for_sixty_seconds(self) -> None:
        self.assertEqual(self.sensors["ir_kz2_surplus_ready"]["delay_on"], {"seconds": 60})
        for grid, expected in (("-901", True), ("-900", False), ("-500", False)):
            with self.subTest(grid=grid):
                self.states[GRID] = grid
                self.assertEqual(self.ready(), expected)
        self.states[GRID] = "-1000"
        self.states[POWER] = "800"
        self.assertFalse(self.ready())

    def test_start_requires_disabled_thermostat_and_qualified_inputs(self) -> None:
        self.assertTrue(self.ready())
        for entity, value in ((ENABLED, "off"), (CLIMATE, "heat"), (POWER, "0"), (SOC, "98.9"), (BATTERY, "0.05")):
            with self.subTest(entity=entity):
                original = self.states[entity]
                self.states[entity] = value
                self.assertFalse(self.ready())
                self.states[entity] = original

    def test_soc_and_grid_hysteresis_keep_existing_heat_without_requalifying(self) -> None:
        self.states.update({CLIMATE: "heat", SOC: "98", GRID: "-200"})
        self.assertFalse(self.ready())
        self.assertFalse(self.stop())
        self.assertIsNone(self.decision())

    def test_stop_thresholds_and_battery_predicates_do_not_overlap(self) -> None:
        for entity, value, expected in (
            (GRID, "-101", False),
            (GRID, "-100", True),
            (SOC, "97", False),
            (SOC, "96.9", True),
            (BATTERY, "0", False),
            (BATTERY, "0.05", True),
            (BATTERY, "-10", False),
        ):
            with self.subTest(entity=entity, value=value):
                original = self.states[entity]
                self.states[entity] = value
                self.assertEqual(self.stop(), expected)
                self.assertFalse(self.ready() and self.stop())
                self.states[entity] = original

    def test_non_numeric_required_inputs_prevent_start_and_request_stop(self) -> None:
        for entity in (GRID, SOC, BATTERY, TEMPERATURE, POWER):
            original = self.states[entity]
            for value in ("unknown", "unavailable"):
                with self.subTest(entity=entity, value=value):
                    self.states[entity] = value
                    self.assertFalse(self.ready())
                    self.assertTrue(self.stop())
            self.states[entity] = original

    def test_qualified_start_explicitly_selects_heat_and_target(self) -> None:
        self.assertIsNone(self.decision(), "Raw surplus must not bypass the delayed binary sensor")
        self.states[READY] = "on"
        action = self.required_action()
        self.assertEqual(action["service"], "climate.set_temperature")
        self.assertEqual(action["data"], {"hvac_mode": "heat", "temperature": 21.5})
        self.assertEqual(action["target"], {"entity_id": CLIMATE})

    def test_stop_explicitly_selects_off_even_with_old_ready_state(self) -> None:
        self.states.update({CLIMATE: "heat", STOP: "on", READY: "on"})
        action = self.required_action()
        self.assertEqual(action["service"], "climate.set_hvac_mode")
        self.assertEqual(action["data"], {"hvac_mode": "off"})
        self.states[STOP] = "unavailable"
        self.assertEqual(self.required_action()["data"], {"hvac_mode": "off"})

    def test_disabling_switches_off_once_then_respects_manual_mode(self) -> None:
        self.states.update({ENABLED: "off", CLIMATE: "heat", STOP: "on"})
        self.assertEqual(self.required_action("control_toggle")["data"], {"hvac_mode": "off"})
        self.assertIsNone(self.decision())
        self.assertIsNone(self.decision("startup"))

    def test_enabling_switches_off_first_to_require_new_qualification(self) -> None:
        self.states[CLIMATE] = "heat"
        self.assertEqual(self.required_action("control_toggle")["data"], {"hvac_mode": "off"})

    def test_periodic_trigger_does_not_unconditionally_shutdown(self) -> None:
        self.states.update({CLIMATE: "heat", STOP: "off"})
        self.assertIsNone(self.decision())
        automation = self.package["automation"][0]
        self.assertEqual(len(self.package["automation"]), 1)
        self.assertTrue(
            any(t.get("platform") == "time_pattern" and t.get("seconds") == "/30" for t in automation["trigger"])
        )


if __name__ == "__main__":
    unittest.main()
