"""Meter semantics without I/O glue: topic mapping, value conversion, staleness.

Pure logic used by the bridge process (app.py) and the unit tests; the only
indirection is the MeterBackend interface that marks where values are stored.
"""

import math
from typing import Callable, Dict, Optional, Protocol, Tuple

from config import BridgeConfig, MeterMapping

TopicTarget = Tuple[str, Callable[[str], float]]
TopicMap = Dict[str, TopicTarget]
PayloadConverter = Callable[[str], float]

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

# Everything the D-Bus backend registers; mapping dbus_path options must be among these.
REGISTERED_DBUS_PATHS = ("/Ac/Energy/Forward", "/Ac/Energy/Reverse") + INSTANTANEOUS_PATHS

# Legacy fallback: without [mapping.*] sections these ESPHome sensor topics
# are derived from the configured topic prefix. Must match electric_meter_ir.yaml.
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


class MeterBackend(Protocol):
    """What the bridge needs from its value sink: a Victron D-Bus meter in production."""

    registered_paths: Tuple[str, ...]

    def set_value(self, path: str, value: Optional[float]) -> None: ...

    def set_connected(self, connected: bool) -> None: ...


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


def load_mappings(config: BridgeConfig, registered_paths: tuple) -> TopicMap:
    """Resolve the config's mapping entries into {topic: (dbus_path, converter)}.

    Without [mapping.*] sections the default ESPHome sensor topics for the
    configured topic_prefix are used, matching historic behaviour.
    """
    entries: Tuple[MeterMapping, ...] = config.mappings or tuple(
        MeterMapping(
            topic="%s/sensor/%s/state" % (config.mqtt.topic_prefix, suffix),
            dbus_path=path,
            converter=name,
        )
        for suffix, path, name in DEFAULT_MAPPINGS
    )
    topic_map: TopicMap = {}
    for mapping in entries:
        if mapping.dbus_path not in registered_paths:
            raise ValueError(
                "dbus_path %r for topic %r is not a registered D-Bus path" % (mapping.dbus_path, mapping.topic)
            )
        topic_map[mapping.topic] = (
            mapping.dbus_path,
            converter_for(mapping.converter, config.meter.power_multiplier),
        )
    return topic_map


def resolve_message(topic_map: TopicMap, topic: str, payload: str, retained: bool) -> Optional[Tuple[str, float]]:
    """Convert one MQTT payload, or None when the payload must be ignored."""
    if topic not in topic_map:
        return None
    if retained:
        print("Ignoring retained MQTT payload on %s" % topic, flush=True)
        return None
    path, converter = topic_map[topic]
    try:
        return path, converter(payload)
    except ValueError:
        print("Ignoring non-numeric MQTT payload on %s: %s" % (topic, payload), flush=True)
        return None


class MeterState:
    """Tracks live meter values and expiry; publishes through a MeterBackend."""

    def __init__(self, backend: MeterBackend, stale_seconds: int) -> None:
        self.backend = backend
        self.stale_seconds = stale_seconds
        self.last_updates: Dict[str, float] = {}

    def update_value(self, path: str, value: float, received_at: float, now: float) -> None:
        if path in INSTANTANEOUS_PATHS:
            # A busy main loop must not make a queued, old sample fresh again.
            if now - received_at > self.stale_seconds:
                return
            self.last_updates[path] = received_at
        self.backend.set_value(path, value)
        if path == POWER_PATH:
            self.backend.set_connected(True)

    def invalidate(self) -> None:
        self.backend.set_connected(False)
        for path in INSTANTANEOUS_PATHS:
            self.backend.set_value(path, None)
        self.last_updates.clear()

    def check_stale(self, now: float) -> None:
        last_power = self.last_updates.get(POWER_PATH)
        if last_power is not None and now - last_power > self.stale_seconds:
            self.invalidate()
            return
        for path, received_at in list(self.last_updates.items()):
            if now - received_at > self.stale_seconds:
                self.backend.set_value(path, None)
                del self.last_updates[path]
