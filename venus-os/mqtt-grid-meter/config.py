"""Typed configuration for the mqtt-grid-meter bridge.

configparser stays at the edge of this module; everything else receives
frozen settings objects. Deployed on Venus OS, so the standard library only.
"""

import configparser
from dataclasses import dataclass
from typing import Optional, Tuple


DEFAULT_TOPIC_PREFIX = "electric-meter-ir"


@dataclass(frozen=True)
class MeterMapping:
    topic: str
    dbus_path: str
    converter: str


@dataclass(frozen=True)
class MqttSettings:
    host: str
    port: int
    tls: bool
    ca_file: Optional[str]
    username: Optional[str]
    password: Optional[str]
    keepalive: int
    topic_prefix: str

    @classmethod
    def from_config(cls, config: configparser.ConfigParser) -> "MqttSettings":
        tls = config.getboolean("mqtt", "tls", fallback=False)
        return cls(
            host=config.get("mqtt", "host"),
            port=config.getint("mqtt", "port", fallback=8883 if tls else 1883),
            tls=tls,
            ca_file=config.get("mqtt", "ca_file", fallback="").strip() or None,
            username=config.get("mqtt", "username", fallback="").strip() or None,
            password=config.get("mqtt", "password", fallback="").strip() or None,
            keepalive=config.getint("mqtt", "keepalive", fallback=60),
            topic_prefix=config.get("mqtt", "topic_prefix", fallback=DEFAULT_TOPIC_PREFIX).strip("/"),
        )


@dataclass(frozen=True)
class MeterSettings:
    service_name: str
    device_instance: int
    product_name: str
    custom_name: str
    stale_seconds: int
    power_multiplier: float

    @classmethod
    def from_config(cls, config: configparser.ConfigParser) -> "MeterSettings":
        return cls(
            service_name=config.get("grid_meter", "service_name", fallback="com.victronenergy.grid.mqtt_ir_grid_meter"),
            device_instance=config.getint("grid_meter", "device_instance", fallback=40),
            product_name=config.get("grid_meter", "product_name", fallback="MQTT IR Grid Meter"),
            custom_name=config.get("grid_meter", "custom_name", fallback="Electric Meter IR"),
            stale_seconds=config.getint("grid_meter", "stale_seconds", fallback=20),
            power_multiplier=config.getfloat("grid_meter", "power_multiplier", fallback=1.0),
        )


@dataclass(frozen=True)
class BridgeConfig:
    mqtt: MqttSettings
    meter: MeterSettings
    # None keeps the built-in ESPHome default topics; any [mapping.*] section
    # switches to explicit mappings.
    mappings: Optional[Tuple[MeterMapping, ...]]

    @classmethod
    def from_config(cls, config: configparser.ConfigParser) -> "BridgeConfig":
        mappings = _read_mappings(config)
        return cls(
            mqtt=MqttSettings.from_config(config),
            meter=MeterSettings.from_config(config),
            mappings=tuple(mappings) if mappings else None,
        )


def _read_mappings(config: configparser.ConfigParser) -> Tuple[MeterMapping, ...]:
    prefix = config.get("mqtt", "topic_prefix", fallback=DEFAULT_TOPIC_PREFIX).strip("/")
    seen: Tuple[str, ...] = ()
    mappings: Tuple[MeterMapping, ...] = ()
    for name in [section for section in config.sections() if section.startswith("mapping.")]:
        topic = config.get(name, "topic", fallback="").strip("/")
        if not topic:
            raise ValueError("[%s] is missing the topic option" % name)
        if not topic.startswith(prefix + "/"):
            raise ValueError("Mapping topic %r must live under the topic_prefix %r" % (topic, prefix))
        path = config.get(name, "dbus_path", fallback="").strip()
        if not path:
            raise ValueError("[%s] is missing the dbus_path option" % name)
        if topic in seen:
            raise ValueError("Duplicate mapping topic %r" % topic)
        seen += (topic,)
        mappings += (
            MeterMapping(
                topic=topic,
                dbus_path=path,
                converter=config.get(name, "converter", fallback="float").strip(),
            ),
        )
    return mappings
