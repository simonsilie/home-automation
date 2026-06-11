#!/usr/bin/env python3
import argparse
import configparser
import os
import signal
import sys
import threading
import time

sys.path.insert(0, "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python")

import dbus.mainloop.glib
from gi.repository import GLib
from vedbus import VeDbusService

from simple_mqtt import SimpleMqttClient


VERSION = "0.1.1"


class GridMeterService:
    def __init__(self, config):
        self.config = config
        self.topic_prefix = config.get("mqtt", "topic_prefix", fallback="electric-meter-ir").strip("/")
        self.power_multiplier = config.getfloat("grid_meter", "power_multiplier", fallback=1.0)
        self.stale_seconds = config.getint("grid_meter", "stale_seconds", fallback=90)
        self.last_message = 0
        self.stop_event = threading.Event()

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.mainloop = GLib.MainLoop()
        service_name = config.get("grid_meter", "service_name", fallback="com.victronenergy.grid.mqtt_ir_grid_meter")
        self.service = VeDbusService(service_name, register=False)

        self._add_static_paths()
        self.service.register()
        self.topic_map = self._build_topic_map()

    def run(self):
        print("mqtt-grid-meter {} starting with delayed D-Bus registration".format(VERSION), flush=True)
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        GLib.timeout_add_seconds(10, self._check_stale)
        thread = threading.Thread(target=self._mqtt_worker, name="mqtt-worker")
        thread.daemon = True
        thread.start()
        self.mainloop.run()

    def _add_static_paths(self):
        product_name = self.config.get("grid_meter", "product_name", fallback="MQTT IR Grid Meter")
        custom_name = self.config.get("grid_meter", "custom_name", fallback="Electric Meter IR")
        device_instance = self.config.getint("grid_meter", "device_instance", fallback=40)

        self.service.add_path("/Mgmt/ProcessName", __file__)
        self.service.add_path("/Mgmt/ProcessVersion", VERSION)
        self.service.add_path("/Mgmt/Connection", "MQTT")
        self.service.add_path("/DeviceInstance", device_instance)
        self.service.add_path("/ProductId", 0xFFFF)
        self.service.add_path("/ProductName", product_name)
        self.service.add_path("/CustomName", custom_name)
        self.service.add_path("/FirmwareVersion", VERSION)
        self.service.add_path("/HardwareVersion", "ESPHome SML over MQTT")
        self.service.add_path("/Connected", 0)
        self.service.add_path("/Position", 0)
        self.service.add_path("/Role", "grid")
        self.service.add_path("/Serial", "electric-meter-ir")
        self.service.add_path("/Ac/Power", None)
        self.service.add_path("/Ac/Frequency", None)
        self.service.add_path("/Ac/Energy/Forward", None)
        self.service.add_path("/Ac/Energy/Reverse", None)

        for phase in ("L1", "L2", "L3"):
            self.service.add_path("/Ac/%s/Power" % phase, None)
            self.service.add_path("/Ac/%s/Voltage" % phase, None)
            self.service.add_path("/Ac/%s/Current" % phase, None)

    def _build_topic_map(self):
        prefix = self.topic_prefix
        return {
            "%s/sensor/active_power/state" % prefix: ("/Ac/Power", self._power),
            "%s/sensor/active_power_l1/state" % prefix: ("/Ac/L1/Power", self._power),
            "%s/sensor/active_power_l2/state" % prefix: ("/Ac/L2/Power", self._power),
            "%s/sensor/active_power_l3/state" % prefix: ("/Ac/L3/Power", self._power),
            "%s/sensor/total_energy_import/state" % prefix: ("/Ac/Energy/Forward", self._float),
            "%s/sensor/total_energy_export/state" % prefix: ("/Ac/Energy/Reverse", self._float),
            "%s/sensor/voltage_l1/state" % prefix: ("/Ac/L1/Voltage", self._float),
            "%s/sensor/voltage_l2/state" % prefix: ("/Ac/L2/Voltage", self._float),
            "%s/sensor/voltage_l3/state" % prefix: ("/Ac/L3/Voltage", self._float),
            "%s/sensor/current_l1/state" % prefix: ("/Ac/L1/Current", self._float),
            "%s/sensor/current_l2/state" % prefix: ("/Ac/L2/Current", self._float),
            "%s/sensor/current_l3/state" % prefix: ("/Ac/L3/Current", self._float),
            "%s/sensor/grid_frequency/state" % prefix: ("/Ac/Frequency", self._float),
        }

    def _mqtt_worker(self):
        while not self.stop_event.is_set():
            client = None
            try:
                client = self._new_mqtt_client()
                client.connect()
                client.subscribe("%s/#" % self.topic_prefix)
                GLib.idle_add(self._set_connected, 1)
                client.loop_forever(self._on_mqtt_message, self.stop_event)
            except Exception as exc:
                GLib.idle_add(self._set_connected, 0)
                print("MQTT connection failed: %s" % exc, flush=True)
                self.stop_event.wait(10)
            finally:
                if client is not None:
                    client.close()

    def _new_mqtt_client(self):
        host = self.config.get("mqtt", "host")
        port = self.config.getint("mqtt", "port", fallback=1883)
        username = self.config.get("mqtt", "username", fallback="").strip() or None
        password = self.config.get("mqtt", "password", fallback="").strip() or None
        keepalive = self.config.getint("mqtt", "keepalive", fallback=60)
        client_id = "venus-mqtt-grid-meter-%s" % os.uname().nodename
        return SimpleMqttClient(host, port, client_id, username, password, keepalive)

    def _on_mqtt_message(self, topic, payload):
        if topic not in self.topic_map:
            return
        path, converter = self.topic_map[topic]
        try:
            value = converter(payload)
        except ValueError:
            print("Ignoring non-numeric MQTT payload on %s: %s" % (topic, payload), flush=True)
            return
        GLib.idle_add(self._update_value, path, value)

    def _update_value(self, path, value):
        self.service[path] = value
        self.last_message = time.time()
        self.service["/Connected"] = 1
        return False

    def _set_connected(self, value):
        self.service["/Connected"] = value
        return False

    def _check_stale(self):
        if self.last_message and time.time() - self.last_message > self.stale_seconds:
            self.service["/Connected"] = 0
        return True

    def _stop(self, _signum, _frame):
        self.stop_event.set()
        self.mainloop.quit()

    def _float(self, payload):
        return float(str(payload).strip())

    def _power(self, payload):
        return self._float(payload) * self.power_multiplier


def load_config(path):
    config = configparser.ConfigParser()
    if not config.read(path):
        raise SystemExit("Could not read config file: %s" % path)
    return config


def main():
    parser = argparse.ArgumentParser(description="Expose ESPHome MQTT meter values as Venus OS D-Bus grid meter")
    parser.add_argument("--config", default="config.ini")
    args = parser.parse_args()
    GridMeterService(load_config(args.config)).run()


if __name__ == "__main__":
    main()
