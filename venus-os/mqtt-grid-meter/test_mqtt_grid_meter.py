import configparser
import os
import ssl
import struct
import sys
import types
import unittest
from typing import Callable
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

import mqtt_grid_meter
from simple_mqtt import MqttPublish, SimpleMqttClient


class MqttConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.meter = mqtt_grid_meter.GridMeterService.__new__(mqtt_grid_meter.GridMeterService)
        self.meter.config = configparser.ConfigParser()
        self.meter.config.read_dict({"mqtt": {"host": "broker.example.com"}})
        self.default_context_factory = ssl.create_default_context
        patcher = mock.patch.object(mqtt_grid_meter.ssl, "create_default_context")
        self.create_context = patcher.start()
        self.addCleanup(patcher.stop)

    def test_existing_config_defaults_to_plaintext_1883(self) -> None:
        client = self.meter._new_mqtt_client()

        self.assertEqual(client.host, "broker.example.com")
        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.assertIsNone(client.username)
        self.assertIsNone(client.password)
        self.assertEqual(client.keepalive, 60)
        self.create_context.assert_not_called()

    def test_explicit_plaintext_without_port_defaults_to_1883(self) -> None:
        self.meter.config["mqtt"]["tls"] = "false"

        client = self.meter._new_mqtt_client()

        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.create_context.assert_not_called()

    def test_legacy_config_with_explicit_1883_stays_plaintext(self) -> None:
        self.meter.config["mqtt"]["port"] = "1883"

        client = self.meter._new_mqtt_client()

        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.create_context.assert_not_called()

    def test_explicit_plaintext_ignores_ca_and_preserves_settings(self) -> None:
        self.meter.config["mqtt"].update(
            tls="false", port="1883", ca_file="/unused/ca.pem", username="reader", password="test-only", keepalive="30"
        )

        client = self.meter._new_mqtt_client()

        self.assertEqual(client.port, 1883)
        self.assertIsNone(client.ssl_context)
        self.assertEqual(client.username, "reader")
        self.assertEqual(client.password, "test-only")
        self.assertEqual(client.keepalive, 30)
        self.create_context.assert_not_called()

    def test_tls_defaults_to_8883_and_system_trust(self) -> None:
        self.meter.config["mqtt"]["tls"] = "true"

        client = self.meter._new_mqtt_client()

        self.assertEqual(client.port, 8883)
        self.create_context.assert_called_once_with(cafile=None)
        self.assertIs(client.ssl_context, self.create_context.return_value)
        assert client.ssl_context is not None
        self.assertEqual(client.ssl_context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_tls_loads_configured_ca_file(self) -> None:
        self.meter.config["mqtt"].update(tls="true", ca_file=" /data/etc/mqtt-grid-meter/broker-ca.pem ")

        client = self.meter._new_mqtt_client()

        self.create_context.assert_called_once_with(cafile="/data/etc/mqtt-grid-meter/broker-ca.pem")
        self.assertIs(client.ssl_context, self.create_context.return_value)

    def test_blank_ca_file_uses_system_trust(self) -> None:
        self.meter.config["mqtt"].update(tls="true", ca_file="  ")

        self.meter._new_mqtt_client()

        self.create_context.assert_called_once_with(cafile=None)

    def test_explicit_ports_are_preserved_independently_of_tls(self) -> None:
        for tls in ("true", "false"):
            with self.subTest(tls=tls):
                self.meter.config["mqtt"].update(tls=tls, port="28883")

                self.assertEqual(self.meter._new_mqtt_client().port, 28883)

    def test_invalid_ca_fails_without_plaintext_fallback(self) -> None:
        self.meter.config["mqtt"].update(tls="true", ca_file="/missing/ca.pem")
        for error in (FileNotFoundError("CA file missing"), ssl.SSLError("invalid CA file")):
            with self.subTest(error=type(error).__name__):
                self.create_context.side_effect = error
                with mock.patch.object(mqtt_grid_meter, "SimpleMqttClient") as client_class:
                    with self.assertRaises(type(error)) as raised:
                        self.meter._new_mqtt_client()
                    self.assertIs(raised.exception, error)
                    client_class.assert_not_called()

    def test_invalid_tls_setting_is_not_treated_as_plaintext(self) -> None:
        self.meter.config["mqtt"]["tls"] = "invalid"

        with self.assertRaises(ValueError):
            self.meter._new_mqtt_client()

        self.create_context.assert_not_called()

    def test_real_default_context_requires_certificate_and_hostname_verification(self) -> None:
        self.create_context.side_effect = self.default_context_factory
        self.meter.config["mqtt"]["tls"] = "true"

        client = self.meter._new_mqtt_client()

        assert client.ssl_context is not None
        self.assertTrue(client.ssl_context.check_hostname)
        self.assertEqual(client.ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(client.ssl_context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_example_selects_tls_dns_hostname_and_port_8883(self) -> None:
        config_path = os.path.join(os.path.dirname(__file__), "config.example.ini")
        self.meter.config = mqtt_grid_meter.load_config(config_path)

        client = self.meter._new_mqtt_client()

        self.assertEqual(client.host, "mqtt.example.com")
        self.assertEqual(client.port, 8883)
        self.assertIs(client.ssl_context, self.create_context.return_value)
        self.create_context.assert_called_once_with(cafile=None)


class GridMeterServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.meter: mqtt_grid_meter.GridMeterService
        self.meter = mqtt_grid_meter.GridMeterService.__new__(mqtt_grid_meter.GridMeterService)
        self.meter.service = {"/Connected": 0}
        self.meter.last_updates = {}
        self.meter.stale_seconds = 20
        self.meter.power_multiplier = 1.0
        self.meter.topic_map = {
            "meter/power": (mqtt_grid_meter.POWER_PATH, self.meter._power),
        }

    def test_retained_power_does_not_connect_meter(self) -> None:
        self.meter._on_mqtt_message("meter/power", "-5000", retained=True)

        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertNotIn(mqtt_grid_meter.POWER_PATH, self.meter.service)

    def test_fresh_total_power_connects_meter(self) -> None:
        with mock.patch.object(mqtt_grid_meter, "GLib", FakeGLib):
            self.meter._on_mqtt_message("meter/power", "-5000", retained=False)

        self.assertEqual(self.meter.service["/Connected"], 1)
        self.assertEqual(self.meter.service[mqtt_grid_meter.POWER_PATH], -5000)
        self.assertGreater(self.meter.last_updates[mqtt_grid_meter.POWER_PATH], 0)

    def test_auxiliary_value_does_not_connect_meter(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=100):
            self.meter._update_value("/Ac/Frequency", 50.0, 100)

        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertNotIn(mqtt_grid_meter.POWER_PATH, self.meter.last_updates)

    def test_stale_power_invalidates_instantaneous_values(self) -> None:
        self.meter.service.update((path, 1) for path in mqtt_grid_meter.INSTANTANEOUS_PATHS)
        self.meter.service["/Connected"] = 1
        self.meter.last_updates = dict.fromkeys(mqtt_grid_meter.INSTANTANEOUS_PATHS, 100.0)
        self.meter.service["/Ac/Energy/Forward"] = 123.4

        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=121):
            self.assertTrue(self.meter._check_stale())

        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertEqual(self.meter.last_updates, {})
        self.assertEqual(self.meter.service["/Ac/Energy/Forward"], 123.4)
        for path in mqtt_grid_meter.INSTANTANEOUS_PATHS:
            self.assertIsNone(self.meter.service[path])

    def test_auxiliary_fields_expire_independently_of_total_power(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=100):
            for path in mqtt_grid_meter.INSTANTANEOUS_PATHS:
                self.meter._update_value(path, 1, 100)
            self.meter._update_value("/Ac/Energy/Forward", 123.4, 100)
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=115):
            self.meter._update_value(mqtt_grid_meter.POWER_PATH, -500, 115)
            self.meter._update_value("/Ac/L1/Voltage", 230, 115)
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=121):
            self.meter._check_stale()

        self.assertEqual(self.meter.service["/Connected"], 1)
        self.assertEqual(self.meter.service[mqtt_grid_meter.POWER_PATH], -500)
        self.assertEqual(self.meter.service["/Ac/L1/Voltage"], 230)
        self.assertEqual(self.meter.service["/Ac/Energy/Forward"], 123.4)
        for path in mqtt_grid_meter.INSTANTANEOUS_PATHS:
            if path not in (mqtt_grid_meter.POWER_PATH, "/Ac/L1/Voltage"):
                self.assertIsNone(self.meter.service[path])
                self.assertNotIn(path, self.meter.last_updates)

    def test_auxiliary_value_expires_before_first_total_power(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=0):
            self.meter._update_value("/Ac/Frequency", 50, 0)
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=21):
            self.meter._check_stale()

        self.assertIsNone(self.meter.service["/Ac/Frequency"])
        self.assertEqual(self.meter.service["/Connected"], 0)

    def test_monotonic_zero_is_a_valid_sample_timestamp(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=0):
            self.meter._update_value(mqtt_grid_meter.POWER_PATH, -500, 0)
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=21):
            self.meter._check_stale()

        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertIsNone(self.meter.service[mqtt_grid_meter.POWER_PATH])

    def test_wall_clock_jumps_do_not_affect_freshness(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=100):
            with mock.patch.object(mqtt_grid_meter.time, "time", return_value=1000):
                self.meter._on_mqtt_message("meter/power", "-500", retained=False)
        for wall_clock in (-1000, 100000):
            with self.subTest(wall_clock=wall_clock):
                with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=110):
                    with mock.patch.object(mqtt_grid_meter.time, "time", return_value=wall_clock):
                        self.meter._check_stale()
                self.assertEqual(self.meter.service["/Connected"], 1)
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=121):
            with mock.patch.object(mqtt_grid_meter.time, "time", return_value=-1000):
                self.meter._check_stale()
        self.assertEqual(self.meter.service["/Connected"], 0)

    def test_queued_sample_uses_receipt_time_not_main_loop_time(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=100):
            with mock.patch.object(mqtt_grid_meter.GLib, "idle_add") as queued:
                self.meter._on_mqtt_message("meter/power", "-500", retained=False)
        queued.assert_called_once_with(self.meter._update_value, mqtt_grid_meter.POWER_PATH, -500, 100)
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=121):
            queued.call_args.args[0](*queued.call_args.args[1:])
        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertNotIn(mqtt_grid_meter.POWER_PATH, self.meter.service)
        self.assertEqual(self.meter.last_updates, {})

    def test_disconnect_clears_timestamps_and_reconnect_requires_total_power(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=100):
            self.meter._update_value(mqtt_grid_meter.POWER_PATH, -500, 100)
        self.meter._invalidate_meter()
        self.assertEqual(self.meter.last_updates, {})
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=101):
            self.meter._update_value("/Ac/Frequency", 50, 101)
            self.assertEqual(self.meter.service["/Connected"], 0)
            self.meter._on_mqtt_message("meter/power", "-500", retained=True)
            self.assertEqual(self.meter.service["/Connected"], 0)
            self.meter._on_mqtt_message("meter/power", "-500", retained=False)
        self.assertEqual(self.meter.service["/Connected"], 1)

    def test_timeout_boundary_preserves_fresh_values(self) -> None:
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=100):
            self.meter._update_value(mqtt_grid_meter.POWER_PATH, -500, 100)
        with mock.patch.object(mqtt_grid_meter.time, "monotonic", return_value=120):
            self.meter._check_stale()
        self.assertEqual(self.meter.service["/Connected"], 1)

    def test_non_finite_values_are_rejected(self) -> None:
        for payload in ("nan", "inf", "-inf"):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.meter._float(payload)


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
