import struct
import sys
import types
import unittest
from unittest import mock


class FakeGLib:
    @staticmethod
    def idle_add(callback, *args):
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
from simple_mqtt import SimpleMqttClient


class GridMeterServiceTests(unittest.TestCase):
    def setUp(self):
        self.meter = mqtt_grid_meter.GridMeterService.__new__(mqtt_grid_meter.GridMeterService)
        self.meter.service = {"/Connected": 0}
        self.meter.last_power_message = 0
        self.meter.stale_seconds = 20
        self.meter.power_multiplier = 1.0
        self.meter.topic_map = {
            "meter/power": (mqtt_grid_meter.POWER_PATH, self.meter._power),
        }

    def test_retained_power_does_not_connect_meter(self):
        self.meter._on_mqtt_message("meter/power", "-5000", retained=True)

        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertNotIn(mqtt_grid_meter.POWER_PATH, self.meter.service)

    def test_fresh_total_power_connects_meter(self):
        with mock.patch.object(mqtt_grid_meter, "GLib", FakeGLib):
            self.meter._on_mqtt_message("meter/power", "-5000", retained=False)

        self.assertEqual(self.meter.service["/Connected"], 1)
        self.assertEqual(self.meter.service[mqtt_grid_meter.POWER_PATH], -5000)
        self.assertGreater(self.meter.last_power_message, 0)

    def test_auxiliary_value_does_not_connect_meter(self):
        self.meter._update_value("/Ac/Frequency", 50.0)

        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertEqual(self.meter.last_power_message, 0)

    def test_stale_power_invalidates_instantaneous_values(self):
        self.meter.service.update((path, 1) for path in mqtt_grid_meter.INSTANTANEOUS_PATHS)
        self.meter.service["/Connected"] = 1
        self.meter.last_power_message = 100

        with mock.patch.object(mqtt_grid_meter.time, "time", return_value=121):
            self.assertTrue(self.meter._check_stale())

        self.assertEqual(self.meter.service["/Connected"], 0)
        self.assertEqual(self.meter.last_power_message, 0)
        for path in mqtt_grid_meter.INSTANTANEOUS_PATHS:
            self.assertIsNone(self.meter.service[path])

    def test_non_finite_values_are_rejected(self):
        for payload in ("nan", "inf", "-inf"):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.meter._float(payload)


class SimpleMqttClientTests(unittest.TestCase):
    def test_decode_publish_reports_retained_flag(self):
        topic = b"meter/power"
        body = struct.pack("!H", len(topic)) + topic + b"-5000"
        client = SimpleMqttClient("localhost", 1883, "test")

        decoded = client._decode_publish(0x31, body)

        self.assertEqual(decoded, ("meter/power", "-5000", 0, None, True))


if __name__ == "__main__":
    unittest.main()