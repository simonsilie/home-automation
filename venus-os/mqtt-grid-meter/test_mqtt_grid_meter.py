import configparser
import os
import ssl
import struct
import sys
import threading
import types
import unittest
from typing import Any, Callable, Dict, Optional
from unittest import mock


class FakeGLib:
    @staticmethod
    def idle_add(callback: Callable[..., object], *args: object) -> object:
        return callback(*args)


dbus_module = types.ModuleType("dbus")
dbus_mainloop_module = types.ModuleType("dbus.mainloop")
dbus_glib_module = types.ModuleType("dbus.mainloop.glib")
dbus_glib_module.__dict__["DBusGMainLoop"] = object
dbus_mainloop_module.__dict__["glib"] = dbus_glib_module
dbus_module.__dict__["mainloop"] = dbus_mainloop_module
sys.modules["dbus"] = dbus_module
sys.modules["dbus.mainloop"] = dbus_mainloop_module
sys.modules["dbus.mainloop.glib"] = dbus_glib_module

gi_module = types.ModuleType("gi")
gi_repository_module = types.ModuleType("gi.repository")
gi_repository_module.__dict__["GLib"] = FakeGLib
gi_module.__dict__["repository"] = gi_repository_module
sys.modules["gi"] = gi_module
sys.modules["gi.repository"] = gi_repository_module

vedbus_module = types.ModuleType("vedbus")
vedbus_module.__dict__["VeDbusService"] = object
sys.modules["vedbus"] = vedbus_module

import meter as meter_module
import app as app_module
import mqtt_grid_meter
from config import BridgeConfig
from simple_mqtt import MqttPublish, SimpleMqttClient

ConfigDict = Dict[str, Dict[str, Any]]


def configparser_from(config_dict: ConfigDict) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read_dict(config_dict)
    return config


def lint_free_example_config() -> configparser.ConfigParser:
    config = mqtt_grid_meter.load_config(os.path.join(os.path.dirname(__file__), "config.example.ini"))
    # Values are intentionally invalid for a live deployment (CHANGE_ME); only
    # option presence matters here.
    return config


class DictMeterBackend:
    """Dict-backed MeterBackend double: values are keyed by D-Bus path."""

    registered_paths: tuple = mqtt_grid_meter.REGISTERED_DBUS_PATHS

    def __init__(self, **preloaded: Optional[float]) -> None:
        self.values: Dict[str, Optional[float]] = {"/Connected": 0, **preloaded}

    def set_value(self, path: str, value: Optional[float]) -> None:
        self.values[path] = value

    def set_connected(self, connected: bool) -> None:
        self.values["/Connected"] = 1 if connected else 0


class MqttConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.meter = mqtt_grid_meter.GridMeterService.__new__(mqtt_grid_meter.GridMeterService)
        self.meter.settings = self.settings({"mqtt": {"host": "broker.example.com"}})
        self.default_context_factory = ssl.create_default_context
        patcher = mock.patch.object(mqtt_grid_meter.ssl, "create_default_context")
        self.create_context = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def settings(config_dict: ConfigDict) -> BridgeConfig:
        return BridgeConfig.from_config(configparser_from(config_dict))

    def client_with(self, **mqtt_overrides: str) -> "SimpleMqttClient":
        self.meter.settings = self.settings(
            {"mqtt": {**{"host": "broker.example.com"}, **mqtt_overrides}, "grid_meter": {}}
        )
        return self.meter.create_mqtt_client()

    def test_existing_config_defaults_to_plaintext_1883(self) -> None:
        client = self.client_with()

        self.assertEqual(client.host, "broker.example.com")
        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.assertIsNone(client.username)
        self.assertIsNone(client.password)
        self.assertEqual(client.keepalive, 60)
        self.create_context.assert_not_called()

    def test_explicit_plaintext_without_port_defaults_to_1883(self) -> None:
        client = self.client_with(tls="false")

        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.create_context.assert_not_called()

    def test_legacy_config_with_explicit_1883_stays_plaintext(self) -> None:
        client = self.client_with(port="1883")

        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.create_context.assert_not_called()

    def test_explicit_plaintext_ignores_ca_and_preserves_settings(self) -> None:
        client = self.client_with(
            tls="false", port="1883", ca_file="/unused/ca.pem", username="reader", password="test-only", keepalive="30"
        )

        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.assertEqual(client.username, "reader")
        self.assertEqual(client.password, "test-only")
        self.assertEqual(client.keepalive, 30)
        self.create_context.assert_not_called()

    def test_tls_defaults_to_8883_and_system_trust(self) -> None:
        client = self.client_with(tls="true")

        self.assertEqual(client.port, 8883)
        self.create_context.assert_called_once_with(cafile=None)
        self.assertIs(client.ssl_context, self.create_context.return_value)
        assert client.ssl_context is not None
        self.assertEqual(client.ssl_context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_tls_loads_configured_ca_file(self) -> None:
        client = self.client_with(tls="true", ca_file=" /data/etc/mqtt-grid-meter/broker-ca.pem ")

        self.create_context.assert_called_once_with(cafile="/data/etc/mqtt-grid-meter/broker-ca.pem")
        self.assertIs(client.ssl_context, self.create_context.return_value)

    def test_blank_ca_file_uses_system_trust(self) -> None:
        self.client_with(tls="true", ca_file="  ")

        self.create_context.assert_called_once_with(cafile=None)

    def test_explicit_ports_are_preserved_independently_of_tls(self) -> None:
        for tls in ("true", "false"):
            with self.subTest(tls=tls):
                client = self.client_with(tls=tls, port="28883")

                self.assertEqual(client.port, 28883)

    def test_invalid_ca_fails_without_plaintext_fallback(self) -> None:
        client_of_errors = []
        for error in (FileNotFoundError("CA file missing"), ssl.SSLError("invalid CA file")):
            with self.subTest(error=type(error).__name__):
                self.create_context.side_effect = error
                with mock.patch.object(mqtt_grid_meter, "SimpleMqttClient") as client_class:
                    with self.assertRaises(type(error)) as raised:
                        client_of_errors.append(self.client_with(tls="true", ca_file="/missing/ca.pem"))
                    self.assertIs(raised.exception, error)
                    client_class.assert_not_called()
        self.assertEqual(client_of_errors, [])

    def test_invalid_tls_setting_is_not_treated_as_plaintext(self) -> None:
        with self.assertRaises(ValueError):
            self.client_with(tls="invalid")

        self.create_context.assert_not_called()

    def test_real_default_context_requires_certificate_and_hostname_verification(self) -> None:
        self.create_context.side_effect = self.default_context_factory

        client = self.client_with(tls="true")

        assert client.ssl_context is not None
        self.assertTrue(client.ssl_context.check_hostname)
        self.assertEqual(client.ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(client.ssl_context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_example_selects_tls_dns_hostname_and_port_8883(self) -> None:
        config = lint_free_example_config()

        self.meter.settings = BridgeConfig.from_config(config)

        client = self.meter.create_mqtt_client()

        self.assertEqual(client.host, "mqtt.example.com")
        self.assertEqual(client.port, 8883)
        self.assertIs(client.ssl_context, self.create_context.return_value)
        self.create_context.assert_called_once_with(cafile=None)


class MeterStateTests(unittest.TestCase):
    """Staleness state machine without GLib or the topic map."""

    def setUp(self) -> None:
        self.backend = DictMeterBackend()
        self.state = meter_module.MeterState(self.backend, 20)

    def test_fresh_total_power_connects_meter(self) -> None:
        self.state.update_value(meter_module.POWER_PATH, -5000.0, received_at=100.0, now=100.0)

        self.assertEqual(self.backend.values["/Connected"], 1)
        self.assertEqual(self.backend.values[meter_module.POWER_PATH], -5000.0)
        self.assertEqual(self.state.last_updates[meter_module.POWER_PATH], 100.0)

    def test_auxiliary_value_does_not_connect_meter(self) -> None:
        self.state.update_value("/Ac/Frequency", 50.0, received_at=100.0, now=100.0)

        self.assertEqual(self.backend.values["/Connected"], 0)
        self.assertNotIn(meter_module.POWER_PATH, self.state.last_updates)
        self.assertEqual(self.backend.values["/Ac/Frequency"], 50.0)
        self.assertEqual(self.state.last_updates["/Ac/Frequency"], 100.0)

    def test_stale_power_invalidates_instantaneous_values(self) -> None:
        self.backend.values.update((path, 1) for path in meter_module.INSTANTANEOUS_PATHS)
        self.backend.values["/Connected"] = 1
        self.state.last_updates = dict.fromkeys(meter_module.INSTANTANEOUS_PATHS, 100.0)
        self.backend.values["/Ac/Energy/Forward"] = 123.4

        self.state.check_stale(121.0)

        self.assertEqual(self.backend.values["/Connected"], 0)
        self.assertEqual(self.state.last_updates, {})
        self.assertEqual(self.backend.values["/Ac/Energy/Forward"], 123.4)
        for path in meter_module.INSTANTANEOUS_PATHS:
            self.assertIsNone(self.backend.values[path])

    def test_auxiliary_fields_expire_independently_of_total_power(self) -> None:
        for path in meter_module.INSTANTANEOUS_PATHS:
            self.state.update_value(path, 1, received_at=100.0, now=100.0)
        self.state.update_value("/Ac/Energy/Forward", 123.4, received_at=100.0, now=100.0)
        self.state.update_value(meter_module.POWER_PATH, -500, received_at=115.0, now=115.0)
        self.state.update_value("/Ac/L1/Voltage", 230, received_at=115.0, now=115.0)
        self.state.check_stale(121.0)

        self.assertEqual(self.backend.values["/Connected"], 1)
        self.assertEqual(self.backend.values[meter_module.POWER_PATH], -500)
        self.assertEqual(self.backend.values["/Ac/L1/Voltage"], 230)
        self.assertEqual(self.backend.values["/Ac/Energy/Forward"], 123.4)
        for path in meter_module.INSTANTANEOUS_PATHS:
            if path not in (meter_module.POWER_PATH, "/Ac/L1/Voltage"):
                self.assertIsNone(self.backend.values[path])
                self.assertNotIn(path, self.state.last_updates)

    def test_auxiliary_value_expires_before_first_total_power(self) -> None:
        self.state.update_value("/Ac/Frequency", 50, received_at=0, now=0)
        self.state.check_stale(21)

        self.assertIsNone(self.backend.values["/Ac/Frequency"])
        self.assertEqual(self.backend.values["/Connected"], 0)

    def test_monotonic_zero_is_a_valid_sample_timestamp(self) -> None:
        self.state.update_value(meter_module.POWER_PATH, -500, received_at=0, now=0)
        self.state.check_stale(21)

        self.assertEqual(self.backend.values["/Connected"], 0)
        self.assertIsNone(self.backend.values[meter_module.POWER_PATH])

    def test_wall_clock_jumps_do_not_affect_freshness(self) -> None:
        self.state.update_value(meter_module.POWER_PATH, -500, received_at=100, now=100)
        for wall_clock in (-1000, 100000):
            with self.subTest(wall_clock=wall_clock):
                self.state.check_stale(110)
                self.assertEqual(self.backend.values["/Connected"], 1)
        self.state.check_stale(121)
        self.assertEqual(self.backend.values["/Connected"], 0)

    def test_disconnect_clears_timestamps_and_reconnect_requires_total_power(self) -> None:
        self.state.update_value(meter_module.POWER_PATH, -500, received_at=100, now=100)
        self.state.invalidate()
        self.assertEqual(self.state.last_updates, {})
        self.state.update_value("/Ac/Frequency", 50, received_at=101, now=101)
        self.assertEqual(self.backend.values["/Connected"], 0)
        self.state.update_value(meter_module.POWER_PATH, -500, received_at=101, now=101)
        self.assertEqual(self.backend.values["/Connected"], 1)

    def test_timeout_boundary_preserves_fresh_values(self) -> None:
        self.state.update_value(meter_module.POWER_PATH, -500, received_at=100, now=100)
        self.state.check_stale(120)
        self.assertEqual(self.backend.values["/Connected"], 1)


class MessageResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service: mqtt_grid_meter.GridMeterService
        self.service = mqtt_grid_meter.GridMeterService.__new__(mqtt_grid_meter.GridMeterService)
        self.service.topic_map = {
            "meter/power": (meter_module.POWER_PATH, meter_module.converter_for("power", 1.0)),
        }

    def default_map(self) -> meter_module.TopicMap:
        return {
            "meter/power": (meter_module.POWER_PATH, meter_module.converter_for("power", 1.0)),
            "meter/aux": (meter_module.POWER_PATH, meter_module.converter_for("float", 1.0)),
        }

    def test_retained_message_is_ignored(self) -> None:
        self.assertIsNone(self.service.resolve_mqtt_message("meter/power", "-5000", retained=True))

    def test_fresh_message_returns_path_and_value(self) -> None:
        outcome = self.service.resolve_mqtt_message("meter/power", "-5000", retained=False)

        self.assertEqual(outcome, (meter_module.POWER_PATH, -5000.0))

    def test_unknown_topic_is_ignored(self) -> None:
        self.assertIsNone(self.service.resolve_mqtt_message("meter/other", "-5000", retained=False))

    def test_non_numeric_payload_is_ignored(self) -> None:
        self.assertIsNone(self.service.resolve_mqtt_message("meter/power", "nan... nope", retained=False))

    def test_power_multiplier_applies(self) -> None:
        settings = MqttConfigTests.settings(
            {"mqtt": {"topic_prefix": "b", "host": "x"}, "grid_meter": {"power_multiplier": "-1"}}
        )
        topic_map = meter_module.load_mappings(settings, meter_module.REGISTERED_DBUS_PATHS)

        outcome = meter_module.resolve_message(topic_map, "b/sensor/active_power/state", "-5000", retained=False)

        assert outcome is not None
        self.assertEqual(outcome[1], 5000.0)


class AppGlueTests(unittest.TestCase):
    """GLib dispatch wiring around the pure meter state."""

    def setUp(self) -> None:
        patcher = mock.patch.object(app_module, "GLib")
        self.GLib = patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = DictMeterBackend()
        self.service: mqtt_grid_meter.GridMeterService
        self.service = mqtt_grid_meter.GridMeterService.__new__(mqtt_grid_meter.GridMeterService)
        self.service.topic_map = {
            "meter/power": (meter_module.POWER_PATH, meter_module.converter_for("power", 1.0)),
        }
        self.service.meter = meter_module.MeterState(self.backend, 20)
        self.service.settings = MqttConfigTests.settings({"mqtt": {"host": "x"}, "grid_meter": {}})
        self.service.stop_event = threading.Event()
        self.app = app_module.BridgeApp(self.service)

    def test_message_is_dispatched_with_receipt_time(self) -> None:
        with (
            mock.patch.object(app_module.GLib, "idle_add") as queued,
            mock.patch.object(app_module.time, "monotonic", return_value=100),
        ):
            self.app._on_mqtt_message("meter/power", "-5000", retained=False)

        queued.assert_called_once_with(self.app._update_value, meter_module.POWER_PATH, -5000.0, 100)

    def test_ignored_message_does_not_queue(self) -> None:
        with mock.patch.object(app_module.GLib, "idle_add") as queued:
            for topic, payload, retained in (
                ("meter/other", "-5000", False),
                ("meter/power", "-5000", True),
                ("meter/power", "bogus", False),
            ):
                self.app._on_mqtt_message(topic, payload, retained)

        queued.assert_not_called()

    def test_queued_sample_uses_receipt_time_not_main_loop_time(self) -> None:
        with (
            mock.patch.object(app_module.GLib, "idle_add", return_value=None) as queued,
            mock.patch.object(app_module.time, "monotonic", return_value=100),
        ):
            self.app._on_mqtt_message("meter/power", "-500", retained=False)
        with mock.patch.object(app_module.time, "monotonic", return_value=121):
            queued.call_args.args[0](*queued.call_args.args[1:])

        self.assertEqual(self.backend.values["/Connected"], 0)
        self.assertNotIn(meter_module.POWER_PATH, self.backend.values)
        self.assertEqual(self.service.meter.last_updates, {})

    def test_check_stale_feeds_monotonic_time_into_the_state(self) -> None:
        self.service.meter = mock.Mock(spec=meter_module.MeterState)
        with mock.patch.object(app_module.time, "monotonic", return_value=1234):
            self.assertTrue(self.app._check_stale())

        self.service.meter.check_stale.assert_called_once_with(1234)

    def test_invalidate_clears_meter_state(self) -> None:
        self.app._update_value(meter_module.POWER_PATH, -500, 100)
        keep_running = self.app._invalidate_meter()

        self.assertFalse(keep_running)
        self.assertEqual(self.backend.values["/Connected"], 0)
        self.assertEqual(self.service.meter.last_updates, {})


class MappingTests(unittest.TestCase):
    def bridge_config(self, config_dict: ConfigDict) -> BridgeConfig:
        return BridgeConfig.from_config(configparser_from(config_dict))

    def mapping_config(self, config_dict: ConfigDict) -> BridgeConfig:
        combined: Dict[str, Dict[str, Any]] = {
            "mqtt": {**{"topic_prefix": "electric-meter-ir", "host": "x"}, **config_dict.get("mqtt", {})},
            "grid_meter": config_dict.get("grid_meter", {}),
        }
        combined.update(config_dict.get("mappings", {}))
        return self.bridge_config(combined)

    def load(self, config_dict: ConfigDict) -> mqtt_grid_meter.TopicMap:
        return mqtt_grid_meter.load_mappings(self.mapping_config(config_dict), mqtt_grid_meter.REGISTERED_DBUS_PATHS)

    def example_config(self) -> mqtt_grid_meter.TopicMap:
        config = lint_free_example_config()

        return mqtt_grid_meter.load_mappings(BridgeConfig.from_config(config), mqtt_grid_meter.REGISTERED_DBUS_PATHS)

    def mapping_sections(self, *sections: Dict[str, str], **overrides: str) -> ConfigDict:
        return {
            "mappings": {("mapping.%s" % index): dict(section) for index, section in enumerate(sections, start=1)},
            "grid_meter": {key: value for key, value in overrides.items()},
        }

    def test_no_mapping_sections_use_default_esphome_topics(self) -> None:
        topic_map = self.load({})

        self.assertEqual(len(topic_map), len(meter_module.DEFAULT_MAPPINGS))
        path, active_power = topic_map["electric-meter-ir/sensor/active_power/state"]
        self.assertEqual(path, "/Ac/Power")
        self.assertEqual(active_power("-5000"), -5000.0)
        path, energy_import = topic_map["electric-meter-ir/sensor/total_energy_import/state"]
        self.assertEqual(path, "/Ac/Energy/Forward")
        self.assertEqual(energy_import("123.4"), 123.4)

    def test_default_topics_follow_topic_prefix(self) -> None:
        topic_map = self.load({"mqtt": {"topic_prefix": "other/prefix"}})

        self.assertIn("other/prefix/sensor/active_power/state", topic_map)

    def test_example_config_without_mapped_sections_still_builds_defaults(self) -> None:
        topic_map = self.example_config()

        self.assertIn("electric-meter-ir/sensor/active_power/state", topic_map)
        self.assertIn("electric-meter-ir/sensor/grid_frequency/state", topic_map)

    def test_explicit_mapping_sections_are_used(self) -> None:
        config = self.mapping_sections(
            {"topic": "electric-meter-ir/sensor/active_power/state", "dbus_path": "/Ac/Power", "converter": "power"},
            {
                "topic": "electric-meter-ir/sensor/total_energy_import/state",
                "dbus_path": "/Ac/Energy/Forward",
                "converter": "float",
            },
            power_multiplier="-1",
        )
        topic_map = self.load(config)

        self.assertEqual(len(topic_map), 2)
        path, converter = topic_map["electric-meter-ir/sensor/active_power/state"]
        self.assertEqual(path, "/Ac/Power")
        self.assertEqual(converter("-5000"), 5000.0)
        path, converter = topic_map["electric-meter-ir/sensor/total_energy_import/state"]
        self.assertEqual(path, "/Ac/Energy/Forward")
        self.assertEqual(converter("123.4"), 123.4)

    def test_converter_defaults_to_float(self) -> None:
        topic_map = self.load(
            self.mapping_sections(
                {"topic": "electric-meter-ir/sensor/grid_frequency/state", "dbus_path": "/Ac/Frequency"}
            )
        )

        path, converter = topic_map["electric-meter-ir/sensor/grid_frequency/state"]
        self.assertEqual(path, "/Ac/Frequency")
        self.assertEqual(converter("50.1"), 50.1)

    def test_duplicate_topics_are_rejected(self) -> None:
        config = self.mapping_sections(
            {"topic": "electric-meter-ir/sensor/active_power/state", "dbus_path": "/Ac/Power"},
            {"topic": "electric-meter-ir/sensor/active_power/state", "dbus_path": "/Ac/L1/Power"},
        )

        with self.assertRaisesRegex(ValueError, "Duplicate mapping topic"):
            self.mapping_config(config)

    def test_unknown_converter_is_rejected(self) -> None:
        config = self.mapping_sections(
            {"topic": "electric-meter-ir/sensor/active_power/state", "dbus_path": "/Ac/Power", "converter": "int"}
        )

        with self.assertRaisesRegex(ValueError, "Unknown converter"):
            self.load(config)

    def test_unregistered_dbus_path_is_rejected(self) -> None:
        config = self.mapping_sections(
            {"topic": "electric-meter-ir/sensor/active_power/state", "dbus_path": "/Dc/Power"}
        )

        with self.assertRaisesRegex(ValueError, "not a registered D-Bus path"):
            self.load(config)

    def test_missing_topic_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing the topic option"):
            self.mapping_config(self.mapping_sections({"dbus_path": "/Ac/Power"}))

    def test_missing_dbus_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing the dbus_path option"):
            self.mapping_config(self.mapping_sections({"topic": "electric-meter-ir/sensor/active_power/state"}))

    def test_topic_outside_topic_prefix_is_rejected(self) -> None:
        config = self.mapping_sections({"topic": "other-device/sensor/active_power/state", "dbus_path": "/Ac/Power"})

        with self.assertRaisesRegex(ValueError, "topic_prefix"):
            self.mapping_config(config)


class SimpleMqttClientTests(unittest.TestCase):
    def test_decode_publish_reports_retained_flag(self) -> None:
        topic = b"meter/power"
        body = struct.pack("!H", len(topic)) + topic + b"-5000"
        client = SimpleMqttClient("localhost", 1883, "test")

        decoded = client._decode_publish(0x31, body)

        self.assertEqual(
            decoded,
            MqttPublish(topic="meter/power", payload="-5000", qos=0, packet_id=None, retained=True),
        )


if __name__ == "__main__":
    unittest.main()
