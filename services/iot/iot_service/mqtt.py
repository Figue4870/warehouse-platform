"""Cliente MQTT 3.1.1 mínimo para los sensores simulados: CONNECT, PUBLISH con QoS 1 (espera PUBACK) y DISCONNECT."""

import socket
import struct


class MqttError(Exception):
    pass


def _remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        byte, length = length % 128, length // 128
        out.append(byte | (0x80 if length else 0))
        if not length:
            return bytes(out)


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("!H", len(data)) + data


class MqttClient:
    def __init__(self, host: str, port: int = 1883, client_id: str = "sensor", username: str | None = None,
                 password: str | None = None, keepalive: int = 60, timeout: float = 5.0):
        self.host, self.port, self.client_id = host, port, client_id
        self.username, self.password, self.keepalive, self.timeout = username, password, keepalive, timeout
        self.sock: socket.socket | None = None
        self.packet_id = 0

    def _recv(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise MqttError("El broker cerró la conexión")
            data += chunk
        return data

    def _read_packet(self) -> tuple[int, bytes]:
        header = self._recv(1)[0]
        multiplier, length = 1, 0
        while True:
            byte = self._recv(1)[0]
            length += (byte & 0x7F) * multiplier
            if not byte & 0x80:
                break
            multiplier *= 128
        return header >> 4, self._recv(length) if length else b""

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        flags = 0x02  # clean session
        payload = _string(self.client_id)
        if self.username:
            flags |= 0x80
            payload += _string(self.username)
            if self.password is not None:
                flags |= 0x40
                payload += _string(self.password)
        variable = _string("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive)
        body = variable + payload
        self.sock.sendall(bytes([0x10]) + _remaining_length(len(body)) + body)
        kind, data = self._read_packet()
        if kind != 2 or len(data) < 2 or data[1] != 0:
            raise MqttError(f"CONNACK rechazado (código {data[1] if len(data) > 1 else '?'})")

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> None:
        if self.sock is None:
            raise MqttError("Sin conexión")
        body = _string(topic)
        if qos:
            self.packet_id = self.packet_id % 65535 + 1
            body += struct.pack("!H", self.packet_id)
        body += payload
        self.sock.sendall(bytes([0x30 | (qos << 1)]) + _remaining_length(len(body)) + body)
        if qos:
            while True:
                kind, data = self._read_packet()
                if kind == 4 and struct.unpack("!H", data[:2])[0] == self.packet_id:
                    return  # PUBACK: el broker la guardó

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.sendall(b"\xe0\x00")
            except OSError:
                pass
            self.sock.close()
            self.sock = None
