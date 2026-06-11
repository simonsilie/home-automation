import socket
import struct
import time


class MqttError(Exception):
    pass


class SimpleMqttClient:
    def __init__(self, host, port, client_id, username=None, password=None, keepalive=60):
        self.host = host
        self.port = int(port)
        self.client_id = client_id
        self.username = username or None
        self.password = password or None
        self.keepalive = int(keepalive)
        self.sock = None
        self.packet_id = 1
        self.last_sent = 0

    def connect(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=15)
        self.sock.settimeout(1)

        flags = 0x02
        payload = self._utf8(self.client_id)
        if self.username is not None:
            flags |= 0x80
            payload += self._utf8(self.username)
        if self.password is not None:
            flags |= 0x40
            payload += self._utf8(self.password)

        variable_header = self._utf8("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive)
        self._send_packet(0x10, variable_header + payload)

        packet_type, body = self._read_packet(timeout=10)
        if packet_type != 0x20 or len(body) < 2:
            raise MqttError("MQTT broker did not return CONNACK")
        if body[1] != 0:
            raise MqttError("MQTT CONNACK failed with code %s" % body[1])

    def subscribe(self, topic_filter):
        packet_id = self._next_packet_id()
        payload = struct.pack("!H", packet_id) + self._utf8(topic_filter) + bytes([0])
        self._send_packet(0x82, payload)

        deadline = time.time() + 10
        while time.time() < deadline:
            packet_type, body = self._read_packet(timeout=1)
            if packet_type == 0x90 and len(body) >= 3:
                ack_id = struct.unpack("!H", body[:2])[0]
                if ack_id == packet_id:
                    if body[2] == 0x80:
                        raise MqttError("MQTT subscribe rejected for %s" % topic_filter)
                    return
            if packet_type == 0x30:
                continue
        raise MqttError("MQTT subscribe timed out for %s" % topic_filter)

    def loop_forever(self, message_callback, stop_event):
        while not stop_event.is_set():
            try:
                packet_type, body = self._read_packet(timeout=1)
            except socket.timeout:
                self._ping_if_needed()
                continue

            packet_class = packet_type & 0xF0
            if packet_class == 0x30:
                topic, payload, qos, packet_id = self._decode_publish(packet_type, body)
                if qos == 1 and packet_id is not None:
                    self._send_packet(0x40, struct.pack("!H", packet_id))
                message_callback(topic, payload)
            elif packet_class == 0xD0:
                continue

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _ping_if_needed(self):
        if time.time() - self.last_sent >= max(10, self.keepalive // 2):
            self._send_packet(0xC0, b"")

    def _decode_publish(self, packet_type, body):
        if len(body) < 2:
            raise MqttError("Short PUBLISH packet")
        topic_length = struct.unpack("!H", body[:2])[0]
        topic_end = 2 + topic_length
        topic = body[2:topic_end].decode("utf-8")
        qos = (packet_type & 0x06) >> 1
        packet_id = None
        payload_start = topic_end
        if qos:
            packet_id = struct.unpack("!H", body[topic_end:topic_end + 2])[0]
            payload_start += 2
        return topic, body[payload_start:].decode("utf-8", errors="replace"), qos, packet_id

    def _next_packet_id(self):
        packet_id = self.packet_id
        self.packet_id += 1
        if self.packet_id > 65535:
            self.packet_id = 1
        return packet_id

    def _send_packet(self, packet_type, payload):
        self.sock.sendall(bytes([packet_type]) + self._remaining_length(len(payload)) + payload)
        self.last_sent = time.time()

    def _read_packet(self, timeout=1):
        old_timeout = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            first = self._recv_exact(1)[0]
            remaining = self._read_remaining_length()
            body = self._recv_exact(remaining) if remaining else b""
            return first, body
        finally:
            self.sock.settimeout(old_timeout)

    def _read_remaining_length(self):
        multiplier = 1
        value = 0
        while True:
            encoded = self._recv_exact(1)[0]
            value += (encoded & 127) * multiplier
            if encoded & 128 == 0:
                return value
            multiplier *= 128
            if multiplier > 128 * 128 * 128:
                raise MqttError("Malformed MQTT remaining length")

    def _recv_exact(self, length):
        chunks = []
        remaining = length
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise MqttError("MQTT socket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _utf8(value):
        data = str(value).encode("utf-8")
        return struct.pack("!H", len(data)) + data

    @staticmethod
    def _remaining_length(length):
        encoded = bytearray()
        while True:
            digit = length % 128
            length //= 128
            if length > 0:
                digit |= 128
            encoded.append(digit)
            if length == 0:
                return bytes(encoded)
