"""Independent HMI owner. Never constructs DeviceService or opens smoke UART."""
import argparse
import logging
import os
from pathlib import Path
import signal
import socket
import threading
import time
from dataclasses import replace

from config import DEFAULT_CONFIG
from hardware.screen.ipc import DEFAULT_SOCKET, JsonChannel
from screen_service import ScreenService
from experiments.access.serial_screen.screen import normalize_legacy_status_frame

LOGGER = logging.getLogger('ningbo.screen')


class ScreenDaemon:
    def __init__(self, screen, socket_path=DEFAULT_SOCKET, clock=None):
        self.screen = screen
        self.path = Path(socket_path)
        self.clock = clock or time.monotonic
        self.listener = None
        self.channel = None
        self.online = False
        self.next_open = 0
        self.stop_event = threading.Event()

    def start(self):
        # Refuse to replace a socket owned by another live daemon.
        if self.path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(.1)
                probe.connect(str(self.path))
            except ConnectionRefusedError:
                self.path.unlink()
            else:
                raise RuntimeError('串口屏 IPC 已有服务监听')
            finally:
                probe.close()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        os.chmod(str(self.path), 0o600)
        self.listener.listen(1)
        self.listener.setblocking(False)

    def _drop_client(self):
        if self.channel is not None:
            self.channel.close()
        self.channel = None

    def _screen_failed(self, exc):
        LOGGER.warning('屏幕断开，稍后仅重连屏幕：%s', exc)
        self.online = False
        self.next_open = self.clock() + 2
        try:
            self.screen.stop()
        except Exception:
            LOGGER.exception('关闭屏幕串口失败')

    def step(self):
        if self.channel is None:
            try:
                sock, _ = self.listener.accept()
            except BlockingIOError:
                pass
            else:
                self.channel = JsonChannel(sock)
                if self.online:
                    try:
                        self.screen.discard_startup_events()
                        self.channel.queue({'kind': 'ready'})
                    except Exception as exc:
                        self._screen_failed(exc)
        if not self.online and self.clock() >= self.next_open:
            try:
                self.screen.start()
                self.screen.discard_startup_events()
                self.online = True
                LOGGER.info('独立串口屏连接已就绪')
                if self.channel is not None:
                    self.channel.queue({'kind': 'ready'})
            except Exception as exc:
                self._screen_failed(exc)
        if self.channel is not None:
            try:
                messages = self.channel.receive()
                for message in messages:
                    if message.get('kind') != 'write':
                        raise ValueError('不支持的屏幕 IPC 指令')
                    frames = message.get('frames')
                    if not isinstance(frames, list) or len(frames) > 128:
                        raise ValueError('屏幕 IPC 帧数量无效')
                    for value in frames:
                        frame = bytes.fromhex(value)
                        if (len(frame) < 8 or len(frame) > 258 or frame[:2] != b'\x5a\xa5'
                                or frame[3] != 0x82 or frame[2] != len(frame) - 3
                                or (len(frame) - 6) % 2):
                            raise ValueError('只接受合法的 JGUS 写变量帧')
                        if self.online:
                            try:
                                self.screen.sender._write_frame(normalize_legacy_status_frame(frame))
                            except Exception as exc:
                                self._screen_failed(exc)
                                break
                self.channel.flush()
            except (OSError, ValueError, TypeError) as exc:
                LOGGER.info('设备 IPC 连接结束：%s', exc)
                self._drop_client()
        if self.online:
            try:
                events = self.screen.read_screen_events()
            except Exception as exc:
                self._screen_failed(exc)
            else:
                # No client means discard controls; never save/replay actuator commands.
                if self.channel is not None:
                    try:
                        for event in events:
                            self.channel.queue({'kind': 'event', 'event': event,
                                                'created_at': self.clock()})
                        self.channel.flush()
                    except (OSError, ValueError) as exc:
                        LOGGER.warning('按钮 IPC 发送失败，丢弃旧事件：%s', exc)
                        self._drop_client()

    def run(self):
        self.start()
        try:
            while not self.stop_event.is_set():
                self.step()
                self.stop_event.wait(.02)
        finally:
            self.close()

    def close(self):
        self._drop_client()
        self.screen.stop()
        if self.listener is not None:
            self.listener.close()
            self.listener = None
            self.path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=os.environ.get('NINGBO_SCREEN_SOCKET', DEFAULT_SOCKET))
    parser.add_argument('--port', default=DEFAULT_CONFIG.screen.port)
    parser.add_argument('--baudrate', type=int, default=DEFAULT_CONFIG.screen.baudrate)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.port != 'auto' and os.path.realpath(args.port) == os.path.realpath(DEFAULT_CONFIG.smoke.port):
        parser.error('屏幕服务禁止使用烟雾串口')
    config = replace(DEFAULT_CONFIG.screen, port=args.port, baudrate=args.baudrate)
    screen = ScreenService(config=config, excluded_ports=(DEFAULT_CONFIG.smoke.port,))
    daemon = ScreenDaemon(screen, args.socket)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: daemon.stop_event.set())
    daemon.run()


if __name__ == '__main__':
    main()
