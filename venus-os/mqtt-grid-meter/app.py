"""GLib process glue for the mqtt-grid-meter bridge: main loop, timer, signals.

Contains no meter logic; everything meter-related lives in meter.py behind
GridMeterService. Runs on Venus OS, so the standard library plus gi/GLib only.
"""

import signal
import threading
import time
from types import FrameType
from typing import TYPE_CHECKING, Optional

from gi.repository import GLib

from mqtt_grid_meter import VERSION

if TYPE_CHECKING:
    from mqtt_grid_meter import GridMeterService


class BridgeApp:
    """Runs a GridMeterService's I/O loops on GLib."""

    def __init__(self, service: "GridMeterService") -> None:
        self.service = service
        self.stop_event = service.stop_event
        self.mainloop = GLib.MainLoop()

    def run(self) -> None:
        print("mqtt-grid-meter {} starting with delayed D-Bus registration".format(VERSION), flush=True)
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        GLib.timeout_add_seconds(1, self._check_stale)
        thread = threading.Thread(target=self._mqtt_worker, name="mqtt-worker")
        thread.daemon = True
        thread.start()
        self.mainloop.run()

    def _check_stale(self) -> bool:
        self.service.meter.check_stale(time.monotonic())
        return True

    def _mqtt_worker(self) -> None:
        while not self.stop_event.is_set():
            client = None
            try:
                client = self.service.create_mqtt_client()
                client.connect()
                client.subscribe("%s/#" % self.service.settings.mqtt.topic_prefix)
                client.loop_forever(self._on_mqtt_message, self.stop_event)
            except Exception as exc:
                GLib.idle_add(self._invalidate_meter)
                print("MQTT connection failed: %s" % exc, flush=True)
                self.stop_event.wait(10)
            finally:
                if client is not None:
                    client.close()

    def _on_mqtt_message(self, topic: str, payload: str, retained: bool) -> None:
        outcome = self.service.resolve_mqtt_message(topic, payload, retained)
        if outcome is not None:
            received_at = time.monotonic()
            GLib.idle_add(self._update_value, *outcome, received_at)

    def _update_value(self, path: str, value: float, received_at: float) -> bool:
        self.service.meter.update_value(path, value, received_at, now=time.monotonic())
        return False

    def _invalidate_meter(self) -> bool:
        self.service.meter.invalidate()
        return False

    def _stop(self, _signum: int, _frame: Optional[FrameType]) -> None:
        self.stop_event.set()
        self.mainloop.quit()
