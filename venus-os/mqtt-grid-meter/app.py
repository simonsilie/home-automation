"""GLib process glue for the mqtt-grid-meter bridge: main loop, timer, signals.

Contains no meter logic; everything meter-related lives in meter.py behind
GridMeterService. Runs on Venus OS, so the standard library plus gi/GLib only.
"""

import signal
import threading
import time
from types import FrameType
from typing import TYPE_CHECKING, Callable, Optional, Protocol

from gi.repository import GLib

from mqtt_grid_meter import VERSION

if TYPE_CHECKING:
    from mqtt_grid_meter import GridMeterService


class Mainloop(Protocol):
    def run(self) -> None: ...

    def quit(self) -> None: ...


class GlibApi(Protocol):
    """The GLib surface BridgeApp needs; injectable fakes in tests."""

    def MainLoop(self) -> Mainloop: ...

    def idle_add(self, callback: Callable[..., object], *args: object) -> object: ...

    def timeout_add_seconds(self, interval: int, callback: Callable[..., bool]) -> object: ...


class BridgeApp:
    """Runs a GridMeterService's I/O loops on GLib."""

    def __init__(
        self,
        service: "GridMeterService",
        glib: Optional[GlibApi] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self.glib: GlibApi = glib if glib is not None else GLib
        self.clock: Callable[[], float] = clock
        self.stop_event = service.stop_event
        self.mainloop = self.glib.MainLoop()

    def run(self) -> None:
        print("mqtt-grid-meter {} starting with delayed D-Bus registration".format(VERSION), flush=True)
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        self.glib.timeout_add_seconds(1, self._check_stale)
        thread = threading.Thread(target=self._mqtt_worker, name="mqtt-worker")
        thread.daemon = True
        thread.start()
        self.mainloop.run()

    def _check_stale(self) -> bool:
        self.service.meter.check_stale(self.clock())
        return True

    def _mqtt_worker(self) -> None:
        while not self.stop_event.is_set():
            client = None
            try:
                client = self.service.create_mqtt_client()
                client.connect()
                client.subscribe("%s/#" % self.service.mqtt_topic_prefix)
                client.loop_forever(self._on_mqtt_message, self.stop_event)
            except Exception as exc:
                self.glib.idle_add(self._invalidate_meter)
                print("MQTT connection failed: %s" % exc, flush=True)
                self.stop_event.wait(10)
            finally:
                if client is not None:
                    client.close()

    def _on_mqtt_message(self, topic: str, payload: str, retained: bool) -> None:
        outcome = self.service.resolve_mqtt_message(topic, payload, retained)
        if outcome is not None:
            received_at = self.clock()
            self.glib.idle_add(self._update_value, *outcome, received_at)

    def _update_value(self, path: str, value: float, received_at: float) -> bool:
        self.service.meter.update_value(path, value, received_at, now=self.clock())
        return False

    def _invalidate_meter(self) -> bool:
        self.service.meter.invalidate()
        return False

    def _stop(self, _signum: int, _frame: Optional[FrameType]) -> None:
        self.stop_event.set()
        self.mainloop.quit()
