import socket
import threading
import unittest
from typing import List, Optional, Union
from unittest import mock

import simple_mqtt
from simple_mqtt import MqttError, SimpleMqttClient


class SimpleMqttFramingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = SimpleMqttClient("localhost", 1883, "test")
        self.sock = mock.Mock(spec=socket.socket)
        self.sock.gettimeout.return_value = 7.0
        self.client.sock = self.sock

    def _assert_incomplete_packet(self, reads: List[Union[bytes, socket.timeout]]) -> None:
        self.sock.recv.side_effect = reads

        def set_timeout(timeout: Optional[float]) -> None:
            if self.sock.close.called:
                raise OSError("Socket is closed")

        self.sock.settimeout.side_effect = set_timeout
        with self.assertRaisesRegex(MqttError, "Incomplete MQTT packet") as raised:
            self.client._read_packet(timeout=2)

        self.assertIsInstance(raised.exception.__cause__, socket.timeout)
        self.sock.close.assert_called_once_with()
        self.assertIsNone(self.client.sock)
        self.sock.settimeout.assert_called_once_with(2)

    def test_timeout_before_remaining_length_closes_socket(self) -> None:
        self._assert_incomplete_packet([b"\x30", socket.timeout("timed out")])

    def test_timeout_during_remaining_length_closes_socket(self) -> None:
        self._assert_incomplete_packet([b"\x30", b"\x80", socket.timeout("timed out")])

    def test_timeout_before_body_closes_socket(self) -> None:
        self._assert_incomplete_packet([b"\x30", b"\x03", socket.timeout("timed out")])

    def test_timeout_during_partial_body_closes_socket(self) -> None:
        self._assert_incomplete_packet([b"\x30", b"\x03", b"a", socket.timeout("timed out")])
        self.assertEqual(self.sock.recv.call_args_list, [mock.call(1), mock.call(1), mock.call(3), mock.call(2)])

    def test_idle_timeout_leaves_socket_open_and_restores_timeout(self) -> None:
        self.sock.recv.side_effect = socket.timeout("idle")

        with self.assertRaises(socket.timeout):
            self.client._read_packet(timeout=2)

        self.sock.recv.assert_called_once_with(1)
        self.sock.close.assert_not_called()
        self.assertIs(self.client.sock, self.sock)
        self.assertEqual(self.sock.settimeout.call_args_list, [mock.call(2), mock.call(7.0)])

    def test_fragmented_packet_and_following_packet_remain_aligned(self) -> None:
        body = b"xyz" + b"a" * 127
        self.sock.recv.side_effect = [b"\x30", b"\x82", b"\x01", b"x", b"yz", b"a" * 127, b"\xd0", b"\x00"]

        self.assertEqual(self.client._read_packet(timeout=2), (0x30, body))
        self.assertEqual(self.client._read_packet(timeout=2), (0xD0, b""))

        self.assertEqual(
            self.sock.recv.call_args_list,
            [
                mock.call(1),
                mock.call(1),
                mock.call(1),
                mock.call(130),
                mock.call(129),
                mock.call(127),
                mock.call(1),
                mock.call(1),
            ],
        )
        self.sock.close.assert_not_called()
        self.assertEqual(self.sock.settimeout.call_args_list, [mock.call(2), mock.call(7.0)] * 2)

    def test_loop_propagates_partial_packet_error_without_pinging(self) -> None:
        self.sock.recv.side_effect = [b"\x30", b"\x03", b"a", socket.timeout("timed out")]
        stop_event = mock.Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        callback = mock.Mock()

        with mock.patch.object(self.client, "_ping_if_needed") as ping:
            with self.assertRaisesRegex(MqttError, "Incomplete MQTT packet"):
                self.client.loop_forever(callback, stop_event)

        ping.assert_not_called()
        callback.assert_not_called()
        stop_event.is_set.assert_called_once_with()
        self.sock.close.assert_called_once_with()
        self.assertIsNone(self.client.sock)

    def test_loop_treats_first_byte_timeout_as_idle(self) -> None:
        self.sock.recv.side_effect = socket.timeout("idle")
        stop_event = mock.Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        callback = mock.Mock()

        with mock.patch.object(self.client, "_ping_if_needed") as ping:
            self.client.loop_forever(callback, stop_event)

        ping.assert_called_once_with()
        callback.assert_not_called()
        self.sock.close.assert_not_called()
        self.assertIs(self.client.sock, self.sock)


class SimpleMqttTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = SimpleMqttClient("localhost", 1883, "test", keepalive=60)
        self.sock = mock.Mock(spec=socket.socket)
        self.client.sock = self.sock

    def test_keepalive_ignores_wall_clock_jumps(self) -> None:
        with mock.patch.object(simple_mqtt, "time") as clock:
            clock.monotonic.side_effect = [100.0, 129.0, 129.0, 130.0, 130.0]
            clock.time.return_value = 100.0
            self.client._send_packet(0xD0, b"")
            self.assertEqual(self.client.last_sent, 100.0)
            self.sock.sendall.reset_mock()

            clock.time.return_value = 100000.0
            self.client._ping_if_needed()
            self.sock.sendall.assert_not_called()

            clock.time.return_value = -100000.0
            self.client._ping_if_needed()
            self.sock.sendall.assert_not_called()

            self.client._ping_if_needed()
            self.sock.sendall.assert_called_once_with(b"\xc0\x00")
            self.assertEqual(self.client.last_sent, 130.0)
            clock.time.assert_not_called()

    def test_subscription_accepts_ack_using_monotonic_deadline(self) -> None:
        with mock.patch.object(simple_mqtt, "time") as clock:
            clock.monotonic.side_effect = [100.0, 100.0, 101.0, 105.0]
            clock.time.side_effect = AssertionError("Wall clock must not be used")
            with mock.patch.object(self.client, "_read_packet") as read_packet:
                read_packet.side_effect = [(0xD0, b""), (0x90, b"\x00\x01\x00")]
                self.client.subscribe("meter/#")

            self.assertEqual(read_packet.call_args_list, [mock.call(timeout=1), mock.call(timeout=1)])
            clock.time.assert_not_called()

    def test_subscription_expires_using_monotonic_deadline(self) -> None:
        with mock.patch.object(simple_mqtt, "time") as clock:
            clock.monotonic.side_effect = [100.0, 100.0, 101.0, 110.0]
            clock.time.side_effect = AssertionError("Wall clock must not be used")
            with mock.patch.object(self.client, "_read_packet", return_value=(0xD0, b"")) as read_packet:
                with self.assertRaisesRegex(MqttError, "MQTT subscribe timed out"):
                    self.client.subscribe("meter/#")

            read_packet.assert_called_once_with(timeout=1)
            clock.time.assert_not_called()


if __name__ == "__main__":
    unittest.main()
