import socket
import ssl
import threading
import unittest
from typing import List, Optional, Union
from unittest import mock

import simple_mqtt
from simple_mqtt import MqttError, SimpleMqttClient


class SimpleMqttTlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_sock = mock.Mock(spec=socket.socket)
        self.tls_sock = mock.Mock(spec=ssl.SSLSocket)
        for sock in (self.raw_sock, self.tls_sock):
            sock.gettimeout.return_value = 1
            sock.recv.side_effect = [b"\x20", b"\x02", b"\x00\x00"]
        self.context = mock.Mock(spec=ssl.SSLContext)
        self.context.check_hostname = True
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.wrap_socket.return_value = self.tls_sock
        patcher = mock.patch.object(simple_mqtt.socket, "create_connection", return_value=self.raw_sock)
        self.create_connection = patcher.start()
        self.addCleanup(patcher.stop)

    def test_plaintext_remains_the_default(self) -> None:
        client = SimpleMqttClient("localhost", "1883", "test", "user", "password")

        client.connect()

        self.assertIsNone(client.ssl_context)
        self.create_connection.assert_called_once_with(("localhost", 1883), timeout=15)
        self.assertIs(client.sock, self.raw_sock)
        self.raw_sock.sendall.assert_called_once()
        self.raw_sock.close.assert_not_called()
        self.context.wrap_socket.assert_not_called()

    def test_verified_handshake_precedes_credentials_and_uses_broker_hostname(self) -> None:
        client = SimpleMqttClient("broker.example.com", 8883, "test", "user", "password", ssl_context=self.context)

        def wrap(sock: socket.socket, server_hostname: str) -> ssl.SSLSocket:
            self.assertIs(sock, self.raw_sock)
            self.assertEqual(server_hostname, "broker.example.com")
            self.raw_sock.sendall.assert_not_called()
            self.tls_sock.sendall.assert_not_called()
            return self.tls_sock

        self.context.wrap_socket.side_effect = wrap
        client.connect()

        self.create_connection.assert_called_once_with(("broker.example.com", 8883), timeout=15)
        self.context.wrap_socket.assert_called_once_with(self.raw_sock, server_hostname="broker.example.com")
        self.assertIs(client.sock, self.tls_sock)
        self.tls_sock.sendall.assert_called_once()
        connect_packet = self.tls_sock.sendall.call_args.args[0]
        self.assertEqual(connect_packet[0], 0x10)
        self.assertIn(client._utf8("user") + client._utf8("password"), connect_packet)
        self.raw_sock.sendall.assert_not_called()
        self.raw_sock.recv.assert_not_called()
        client.close()
        self.tls_sock.close.assert_called_once_with()
        self.raw_sock.close.assert_not_called()
        self.assertIsNone(client.sock)

    def test_tls_minimum_is_at_least_1_2(self) -> None:
        self.context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        client = SimpleMqttClient("broker.example.com", 8883, "test", ssl_context=self.context)

        client.connect()

        self.assertEqual(self.context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_stronger_tls_minimum_is_not_downgraded(self) -> None:
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        client = SimpleMqttClient("broker.example.com", 8883, "test", ssl_context=self.context)

        client.connect()

        self.assertEqual(self.context.minimum_version, ssl.TLSVersion.TLSv1_3)

    def test_unverified_contexts_are_rejected_before_opening_socket(self) -> None:
        for check_hostname, verify_mode in ((False, ssl.CERT_REQUIRED), (True, ssl.CERT_NONE)):
            with self.subTest(check_hostname=check_hostname, verify_mode=verify_mode):
                self.context.check_hostname = check_hostname
                self.context.verify_mode = verify_mode
                client = SimpleMqttClient("broker.example.com", 8883, "test", ssl_context=self.context)

                with self.assertRaisesRegex(ValueError, "certificate and hostname verification"):
                    client.connect()

                self.create_connection.assert_not_called()
                self.context.wrap_socket.assert_not_called()
                self.assertIsNone(client.sock)

    def test_handshake_failure_closes_raw_socket_without_sending_credentials(self) -> None:
        for error in (ssl.SSLCertVerificationError("untrusted certificate"), ssl.SSLError("handshake failed")):
            with self.subTest(error=type(error).__name__):
                self.raw_sock.reset_mock()
                self.context.wrap_socket.reset_mock()
                self.context.wrap_socket.side_effect = error
                client = SimpleMqttClient(
                    "broker.example.com", 8883, "test", "user", "password", ssl_context=self.context
                )

                with self.assertRaises(type(error)) as raised:
                    client.connect()

                self.assertIs(raised.exception, error)
                self.context.wrap_socket.assert_called_once_with(self.raw_sock, server_hostname="broker.example.com")
                self.raw_sock.close.assert_called_once_with()
                self.raw_sock.sendall.assert_not_called()
                self.tls_sock.sendall.assert_not_called()
                self.assertIsNone(client.sock)

    def test_close_error_does_not_mask_handshake_failure(self) -> None:
        error = ssl.SSLError("handshake failed")
        self.context.wrap_socket.side_effect = error
        self.raw_sock.close.side_effect = OSError("close failed")
        client = SimpleMqttClient("broker.example.com", 8883, "test", ssl_context=self.context)

        with self.assertRaises(ssl.SSLError) as raised:
            client.connect()

        self.assertIs(raised.exception, error)
        self.raw_sock.close.assert_called_once_with()
        self.assertIsNone(client.sock)

    def test_timeout_setup_failure_closes_wrapped_socket(self) -> None:
        self.tls_sock.settimeout.side_effect = OSError("timeout setup failed")
        client = SimpleMqttClient("broker.example.com", 8883, "test", ssl_context=self.context)

        with self.assertRaisesRegex(OSError, "timeout setup failed"):
            client.connect()

        self.tls_sock.close.assert_called_once_with()
        self.raw_sock.sendall.assert_not_called()
        self.tls_sock.sendall.assert_not_called()
        self.assertIsNone(client.sock)


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
