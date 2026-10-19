"""Bounded nonblocking local IPC. Only the screen daemon opens the HMI UART."""
import json
import logging
import os
import socket
import time

from screen_service import MessageParser
from hardware.screen.sender import ScreenSender

LOGGER = logging.getLogger(__name__)
DEFAULT_SOCKET = '/run/ningbo-screen/bridge.sock'
MAX_BUFFER = 262144
MAX_WORDS = 4096


class JsonChannel:
    def __init__(self, sock):
        self.sock = sock
        self.sock.setblocking(False)
        self.incoming = bytearray()
        self.outgoing = bytearray()

    def queue(self, message):
        data = json.dumps(message, ensure_ascii=True).encode('ascii') + b'\n'
        if len(self.outgoing) + len(data) > MAX_BUFFER:
            raise OSError('屏幕 IPC 发送缓冲已满')
        self.outgoing.extend(data)

    def flush(self):
        if self.outgoing:
            try:
                sent = self.sock.send(self.outgoing)
            except BlockingIOError:
                return
            if sent == 0:
                raise OSError('屏幕 IPC 已关闭')
            del self.outgoing[:sent]

    def receive(self):
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            data = None
        if data == b'':
            raise OSError('屏幕 IPC 对端已退出')
        if data:
            self.incoming.extend(data)
        if len(self.incoming) > MAX_BUFFER:
            raise ValueError('屏幕 IPC 接收缓冲过大')
        messages = []
        while b'\n' in self.incoming:
            line, _, rest = self.incoming.partition(b'\n')
            self.incoming = bytearray(rest)
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError('屏幕 IPC 消息必须是对象')
            messages.append(message)
        return messages

    def close(self):
        self.sock.close()


class RemoteScreenService:
    """Backend proxy: offline screens never block or stop sensor sampling.

    Only latest display words survive reconnect; button events are never replayed.
    Returned frame records mean submitted display state, not hardware acknowledgement.
    """
    def __init__(self, voice_config=None, socket_path=None, clock=None):
        self.parser = MessageParser(voice_config)
        self.socket_path = socket_path or os.environ.get('NINGBO_SCREEN_SOCKET', DEFAULT_SOCKET)
        self.clock = clock or time.monotonic
        self.channel = None
        self.latest = {}
        self.dirty = set()
        self.events = []
        self.next_connect = 0
        self.started = False

    def start(self):
        self.started = True
        self._pump()

    def stop(self):
        self.started = False
        self._disconnect()
        self.latest.clear()
        self.dirty.clear()

    def _disconnect(self):
        if self.channel is not None:
            self.channel.close()
        self.channel = None
        self.events.clear()
        self.dirty.update(self.latest)
        self.next_connect = self.clock() + 1

    def _pump(self):
        if not self.started:
            return
        try:
            if self.channel is None:
                if self.clock() < self.next_connect:
                    return
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    # Local connect only; do not stall the device loop on a full backlog.
                    sock.settimeout(0.02)
                    sock.connect(self.socket_path)
                except OSError:
                    sock.close()
                    self.next_connect = self.clock() + 1
                    return
                self.channel = JsonChannel(sock)
                self.dirty.update(self.latest)
                LOGGER.info('设备服务已连接独立串口屏服务')
            for message in self.channel.receive():
                if message.get('kind') == 'ready':
                    self.dirty.update(self.latest)
                    self.events.clear()
                elif message.get('kind') == 'event' and isinstance(message.get('event'), dict):
                    age = self.clock() - message.get('created_at', -100)
                    if not 0 <= age <= 2:
                        continue
                    if len(self.events) >= 64:
                        raise ValueError('屏幕按钮事件过多，断开以丢弃积压事件')
                    self.events.append(message['event'])
            if self.dirty and not self.channel.outgoing:
                addresses = sorted(self.dirty)[:128]
                frames = [ScreenSender.build_write_frame(a, self.latest[a]).hex() for a in addresses]
                self.channel.queue({'kind': 'write', 'frames': frames})
                self.dirty.difference_update(addresses)
            self.channel.flush()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            LOGGER.warning('串口屏 IPC 暂不可用，设备采样继续：%s', exc)
            self._disconnect()

    def read_screen_events(self):
        self._pump()
        events, self.events = self.events, []
        return events

    def discard_startup_events(self):
        self._pump()
        self.events.clear()

    def handle_message(self, message):
        records = []
        for item in self.parser.parse(message):
            address = item['address']
            if 'data' in item:
                frame = ScreenSender.build_write_bytes_frame(address, item['data'])
            else:
                frame = ScreenSender.build_write_frame(address, item['value'])
            words = {address + i // 2: int.from_bytes(frame[6+i:8+i], 'big')
                     for i in range(0, len(frame) - 6, 2)}
            if len(self.latest.keys() | words.keys()) > MAX_WORDS:
                raise ValueError('显示缓存超过允许的 VP 字数')
            self.latest.update(words)
            self.dirty.update(words)
            records.append({**item, 'frame': frame, 'hex': frame.hex(' ').upper(),
                            'delivery': 'queued'})
        self._pump()
        return records
