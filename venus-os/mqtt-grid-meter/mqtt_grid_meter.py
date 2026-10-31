#!/usr/bin/env python3
"""Entry point and composition root: config, backend, topic map, meter state."""

import argparse
import configparser
import os
import ssl
import sys
import threading
import time
from typing import Callable, Optional, Tuple

sys.path.insert(0, "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python")

import dbus.mainloop.glib  # noqa: F401  (setting up the default D-Bus main loop)
import dbus.exceptions  # noqa: F401  (referenced by the name-collision guard in main)
from vedbus import VeDbusService

from config import BridgeConfig, MeterSettings
from meter import MeterBackend, MeterState, REGISTERED_DBUS_PATHS, TopicMap, load_mappings, resolve_message
from simple_mqtt import SimpleMqttClient

VERSION = "0.1.6"


class DbusMeterBackend:
    """MeterBackend implementation backed by a VeDbusService grid meter."""

    registered_paths: Tuple[str, ...] = REGISTERED_DBUS_PATHS

    def __init__(self, meter: MeterSettings) -> None:
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.service = VeDbusService(meter.service_name, register=False)

        self.service.add_path("/Mgmt/ProcessName", __file__)
        self.service.add_path("/Mgmt/ProcessVersion", VERSION)
        self.service.add_path("/Mgmt/Connection", "MQTT")
        self.service.add_path("/DeviceInstance", meter.device_instance)
        self.service.add_path("/ProductId", 0xFFFF)
        self.service.add_path("/ProductName", meter.product_name)
        self.service.add_path("/CustomName", meter.custom_name)
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
    """Composes settings, the D-Bus backend, the topic map and meter state."""

    def __init__(
        self,
        config: configparser.ConfigParser,
        backend: Optional[MeterBackend] = None,
    ) -> None:
        self.settings = BridgeConfig.from_config(config)
        self.backend: MeterBackend = backend if backend is not None else DbusMeterBackend(self.settings.meter)
        self.topic_map: TopicMap = load_mappings(self.settings, self.backend.registered_paths)
        self.meter = MeterState(self.backend, self.settings.meter.stale_seconds)
        self.stop_event = threading.Event()

    def resolve_mqtt_message(self, topic: str, payload: str, retained: bool) -> Optional[Tuple[str, float]]:
        """Convert one MQTT payload, or None when it must be ignored."""
        return resolve_message(self.topic_map, topic, payload, retained)

    def create_mqtt_client(
        self, ssl_context_factory: Callable[..., ssl.SSLContext] = ssl.create_default_context
    ) -> SimpleMqttClient:
        mqtt = self.settings.mqtt
        ssl_context = None
        if mqtt.tls:
            ssl_context = ssl_context_factory(cafile=mqtt.ca_file)
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        client_id = "venus-mqtt-grid-meter-%s" % os.uname().nodename
        return SimpleMqttClient(
            mqtt.host, mqtt.port, client_id, mqtt.username, mqtt.password, mqtt.keepalive, ssl_context=ssl_context
        )

    @property
    def mqtt_topic_prefix(self) -> str:
        """Subscribe filter prefix for BridgeApp's worker thread."""
        return self.settings.mqtt.topic_prefix


def load_config(path: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    if not config.read(path):
        raise SystemExit("Could not read config file: %s" % path)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Expose ESPHome MQTT meter values as Venus OS D-Bus grid meter")
    parser.add_argument("--config", default="config.ini")
    args = parser.parse_args()
    from app import BridgeApp

    try:
        app = BridgeApp(GridMeterService(load_config(args.config)))
    except dbus.exceptions.NameExistsException:
        # daemontools restarts this service on every exit; keep the tarpit log
        # readable while another process holds the bus name (usually a copy
        # whose supervise parent is gone).
        print(
            "D-Bus service name already in use; check for an orphaned "
            "mqtt_grid_meter.py process (one without a supervise parent) and kill it",
            file=sys.stderr,
        )
        time.sleep(60)
        raise
    app.run()


if __name__ == "__main__":
    main()
