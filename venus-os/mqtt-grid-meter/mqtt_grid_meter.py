#!/usr/bin/env python3
import argparse
import configparser
import math
import os
import signal
import ssl
import sys
import threading
import time
from types import FrameType
from typing import Callable, Dict, Optional, Protocol, Tuple

sys.path.insert(0, "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python")

import dbus.mainloop.glib
from gi.repository import GLib
from vedbus import VeDbusService

from simple_mqtt import SimpleMqttClient


PayloadConverter = Callable[[str], float]
TopicTarget = Tuple[str, PayloadConverter]
TopicMap = Dict[str, TopicTarget]

VERSION = "0.1.4"

POWER_PATH = "/Ac/Power"
INSTANTANEOUS_PATHS = (
    POWER_PATH,
    "/Ac/Frequency",
    "/Ac/L1/Power",
    "/Ac/L1/Voltage",
    "/Ac/L1/Current",
    "/Ac/L2/Power",
    "/Ac/L2/Voltage",
    "/Ac/L2/Current",
    "/Ac/L3/Power",
    "/Ac/L3/Voltage",
    "/Ac/L3/Current",
)

# Legacy fallback: without [mapping.*] sections these ESPHome sensor topics
# are derived from [mqtt] topic_prefix. The list must match electric_meter_ir.yaml.
DEFAULT_MAPPINGS = (
    ("active_power", POWER_PATH, "power"),
    ("active_power_l1", "/Ac/L1/Power", "power"),
    ("active_power_l2", "/Ac/L2/Power", "power"),
    ("active_power_l3", "/Ac/L3/Power", "power"),
    ("total_energy_import", "/Ac/Energy/Forward", "float"),
    ("total_energy_export", "/Ac/Energy/Reverse", "float"),
    ("voltage_l1", "/Ac/L1/Voltage", "float"),
    ("voltage_l2", "/Ac/L2/Voltage", "float"),
    ("voltage_l3", "/Ac/L3/Voltage", "float"),
    ("current_l1", "/Ac/L1/Current", "float"),
    ("current_l2", "/Ac/L2/Current", "float"),
    ("current_l3", "/Ac/L3/Current", "float"),
    ("grid_frequency", "/Ac/Frequency", "float"),
)

# Everything _add_static_paths registers; mapping dbus_path options must be among these.
REGISTERED_DBUS_PATHS = ("/Ac/Energy/Forward", "/Ac/Energy/Reverse") + INSTANTANEOUS_PATHS


def converter_for(name: str, power_multiplier: float) -> PayloadConverter:
    def plain_float(payload: str) -> float:
        value = float(payload.strip())
        if not math.isfinite(value):
            raise ValueError("non-finite meter value")
        return value

    def power(payload: str) -> float:
        return plain_float(payload) * power_multiplier

    if name == "float":
        return plain_float
    if name == "power":
        return power
    raise ValueError("Unknown converter %r (expected 'float' or 'power')" % name)


class MeterBackend(Protocol):
    """What the bridge needs from its value sink: a Victron D-Bus meter in production."""

    registered_paths: tuple

    def set_value(self, path: str, value: Optional[float]) -> None: ...

    def set_connected(self, connected: bool) -> None: ...


class DbusMeterBackend:
    """MeterBackend implementation backed by a VeDbusService grid meter."""

    registered_paths: tuple = REGISTERED_DBUS_PATHS

    def __init__(self, config: configparser.ConfigParser) -> None:
        product_name = config.get("grid_meter", "product_name", fallback="MQTT IR Grid Meter")
        custom_name = config.get("grid_meter", "custom_name", fallback="Electric Meter IR")
        device_instance = config.getint("grid_meter", "device_instance", fallback=40)

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        service_name = config.get("grid_meter", "service_name", fallback="com.victronenergy.grid.mqtt_ir_grid_meter")
        self.service = VeDbusService(service_name, register=False)

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
        for path in REGISTERED_DBUS_PATHS:
            self.service.add_path(path, None)
        self.service.register()

    def set_value(self, path: str, value: Optional[float]) -> None:
        self.service[path] = value

    def set_connected(self, connected: bool) -> None:
        self.service["/Connected"] = 1 if connected else 0


class GridMeterService:
    def __init__(self, config: configparser.ConfigParser, backend: Optional[MeterBackend] = None) -> None:
        self.config = config
        self.topic_prefix = config.get("mqtt", "topic_prefix", fallback="electric-meter-ir").strip("/")
        self.stale_seconds = config.getint("grid_meter", "stale_seconds", fallback=20)
        self.last_updates: Dict[str, float] = {}
        self.stop_event = threading.Event()

        self.backend: MeterBackend = backend if backend is not None else DbusMeterBackend(config)
        self.mainloop = GLib.MainLoop()
        self.topic_map = self._build_topic_map()

    def run(self) -> None:
        print("mqtt-grid-meter {} starting with delayed D-Bus registration".format(VERSION), flush=True)
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        GLib.timeout_add_seconds(1, self._check_stale)
        thread = threading.Thread(target=self._mqtt_worker, name="mqtt-worker")
        thread.daemon = True
        thread.start()
        self.mainloop.run()

    def _mqtt_worker(self) -> None:
        while not self.stop_event.is_set():
            client = None
            try:
                client = self._new_mqtt_client()
                client.connect()
                client.subscribe("%s/#" % self.topic_prefix)
                client.loop_forever(self._on_mqtt_message, self.stop_event)
            except Exception as exc:
                GLib.idle_add(self._invalidate_meter)
                print("MQTT connection failed: %s" % exc, flush=True)
                self.stop_event.wait(10)
            finally:
                if client is not None:
                    client.close()

    def _new_mqtt_client(self) -> SimpleMqttClient:
        host = self.config.get("mqtt", "host")
        tls = self.config.getboolean("mqtt", "tls", fallback=False)
        port = self.config.getint("mqtt", "port", fallback=8883 if tls else 1883)
        ssl_context = None
        if tls:
            ca_file = self.config.get("mqtt", "ca_file", fallback="").strip() or None
            ssl_context = ssl.create_default_context(cafile=ca_file)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        username = self.config.get("mqtt", "username", fallback="").strip() or None
        password = self.config.get("mqtt", "password", fallback="").strip() or None
        keepalive = self.config.getint("mqtt", "keepalive", fallback=60)
        client_id = "venus-mqtt-grid-meter-%s" % os.uname().nodename
        return SimpleMqttClient(host, port, client_id, username, password, keepalive, ssl_context=ssl_context)

    def _on_mqtt_message(self, topic: str, payload: str, retained: bool) -> None:
        if topic not in self.topic_map:
            return
        if retained:
            print("Ignoring retained MQTT payload on %s" % topic, flush=True)
            return
        path, converter = self.topic_map[topic]
        try:
            value = converter(payload)
        except ValueError:
            print("Ignoring non-numeric MQTT payload on %s: %s" % (topic, payload), flush=True)
            return
        GLib.idle_add(self._update_value, path, value, time.monotonic())

    def _update_value(self, path: str, value: float, received_at: float) -> bool:
        if path in INSTANTANEOUS_PATHS:
            # A busy main loop must not make a queued, old sample fresh again.
            if time.monotonic() - received_at > self.stale_seconds:
                return False
            self.last_updates[path] = received_at
        self.backend.set_value(path, value)
        if path == POWER_PATH:
            self.backend.set_connected(True)
        return False

    def _invalidate_meter(self) -> bool:
        self.backend.set_connected(False)
        for path in INSTANTANEOUS_PATHS:
            self.backend.set_value(path, None)
        self.last_updates.clear()
        return False

    def _check_stale(self) -> bool:
        now = time.monotonic()
        last_power = self.last_updates.get(POWER_PATH)
        if last_power is not None and now - last_power > self.stale_seconds:
            self._invalidate_meter()
        else:
            for path, received_at in list(self.last_updates.items()):
                if now - received_at > self.stale_seconds:
                    self.backend.set_value(path, None)
                    del self.last_updates[path]
        return True

    def _stop(self, _signum: int, _frame: Optional[FrameType]) -> None:
        self.stop_event.set()
        self.mainloop.quit()

    def _build_topic_map(self) -> TopicMap:
        return load_mappings(
            self.config,
            REGISTERED_DBUS_PATHS,
            self.config.getfloat("grid_meter", "power_multiplier", fallback=1.0),
        )


def load_mappings(config: configparser.ConfigParser, registered_paths: tuple, power_multiplier: float) -> TopicMap:
    """Resolve [mapping.*] sections into {topic: (dbus_path, converter)}.

    Without mapping sections the default ESPHome sensor topics for
    [mqtt] topic_prefix are used, matching historic behaviour.
    """
    prefix = config.get("mqtt", "topic_prefix", fallback="electric-meter-ir").strip("/")
    sections = [name for name in config.sections() if name.startswith("mapping.")]
    if not sections:
        entries = [("%s/sensor/%s/state" % (prefix, suffix), path, conv) for suffix, path, conv in DEFAULT_MAPPINGS]
    else:
        entries = []
        for name in sections:
            topic = config.get(name, "topic", fallback="").strip("/")
            if not topic:
                raise ValueError("[%s] is missing the topic option" % name)
            if not topic.startswith(prefix + "/"):
                raise ValueError("Mapping topic %r must live under the topic_prefix %r" % (topic, prefix))
            path = config.get(name, "dbus_path", fallback="").strip()
            if not path:
                raise ValueError("[%s] is missing the dbus_path option" % name)
            if path not in registered_paths:
                raise ValueError("[%s] dbus_path %r is not a registered D-Bus path" % (name, path))
            entries.append(
                (
                    topic,
                    path,
                    config.get(name, "converter", fallback="float").strip(),
                )
            )

    topic_map: TopicMap = {}
    for topic, path, converter_name in entries:
        if topic in topic_map:
            raise ValueError("Duplicate mapping topic %r" % topic)
        topic_map[topic] = (path, converter_for(converter_name, power_multiplier))
    return topic_map


def load_config(path: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    if not config.read(path):
        raise SystemExit("Could not read config file: %s" % path)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Expose ESPHome MQTT meter values as Venus OS D-Bus grid meter")
    parser.add_argument("--config", default="config.ini")
    args = parser.parse_args()
    GridMeterService(load_config(args.config)).run()


if __name__ == "__main__":
    main()
