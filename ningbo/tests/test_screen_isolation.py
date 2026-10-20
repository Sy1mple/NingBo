"""Exercise real IPC sockets, reconnect, offline startup and command expiry."""
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hardware.screen.ipc import JsonChannel, RemoteScreenService, MAX_BUFFER
from screen_daemon import ScreenDaemon
from config import DEFAULT_CONFIG
from experiments.smoke.jetson.driver import SmokeDriver
from system.diagnostics.jetson.service import DiagnosticService
from system.diagnostics.bridge import DiagnosticsScreenBridge


class FakeScreen:
    def __init__(self):
        self.sender = SimpleNamespace(_write_frame=self.write)
        self.frames = []
        self.events = []
        self.opens = 0
        self.stops = 0
        self.fail_open = False
        self.fail_read = False
        self.fail_write = False

    def write(self, frame):
        if self.fail_write:
            raise OSError('simulated screen write failure')
        self.frames.append(frame)

    def start(self):
        self.opens += 1
        if self.fail_open:
            raise OSError('screen absent')

    def stop(self):
        self.stops += 1

    def discard_startup_events(self):
        self.events.clear()

    def read_screen_events(self):
        if self.fail_read:
            raise OSError('screen disconnected')
        events, self.events = self.events, []
        return events


class ScreenIsolationTests(unittest.TestCase):
    def test_smoke_diagnostic_request_after_screen_restart_keeps_same_uart(self):
        class Uart:
            def __init__(self):
                self.data = bytearray()
                self.closed = False

            @property
            def in_waiting(self):
                return len(self.data)

            def read(self, count):
                result = bytes(self.data[:count])
                del self.data[:count]
                return result

            def close(self):
                self.closed = True

        uart = Uart()
        opens = []

        def factory(**kwargs):
            opens.append(kwargs)
            return uart

        smoke = SmokeDriver(DEFAULT_CONFIG.smoke, serial_factory=factory,
                            clock=lambda: self.now)
        self.addCleanup(smoke.close)
        manager = SimpleNamespace(config=DEFAULT_CONFIG, _started=True,
                                  poll_sensor=lambda name: smoke.read())
        diagnostics = DiagnosticService(manager)
        self.addCleanup(diagnostics.stop)
        bridge = DiagnosticsScreenBridge(self.proxy, SimpleNamespace(
            request_diagnostics=diagnostics.request,
            diagnostic_snapshot=diagnostics.snapshot))
        self.start_daemon()
        self.proxy.start()
        self.cycle()

        for attempt in range(5):
            if attempt:
                self.daemon.close()
                self.daemon = None
                self.cycle()
                self.start_daemon()
                self.now += 2
                self.cycle(30)
            # Exercise the real driver from the sampling path first, then
            # request diagnostics through the actual screen IPC event path.
            uart.data.extend(bytes.fromhex('ffaa015b3c'))
            self.assertEqual(smoke.read()['status'], 'normal')
            self.screen.events.append({'event': 'diagnostics.smoke'})
            events = self.cycle()
            self.assertEqual(len(events), 1)
            self.assertTrue(bridge.handle_event(events[0]['event'])['handled'])
            diagnostics._worker.join(2)
            self.assertFalse(diagnostics.snapshot()['busy'])
            result = diagnostics.snapshot()['results']['smoke']
            self.assertEqual(result['status'], 'normal')
            bridge.tick()
            self.cycle()
            self.assertFalse(uart.closed)
        self.assertEqual(len(opens), 1)
        self.assertEqual(smoke.parser.valid_frames, 5)
        # The diagnostic report is a manual-check snapshot. A stale sensor
        # does not update it until another check is requested.
        self.now += DEFAULT_CONFIG.smoke.stale_after_seconds + 1
        self.assertEqual(smoke.read()['status'], 'error')
        self.assertEqual(diagnostics.snapshot()['results']['smoke']['status'], 'normal')
        self.screen.events.append({'event': 'diagnostics.smoke'})
        events = self.cycle()
        self.assertTrue(bridge.handle_event(events[0]['event'])['handled'])
        diagnostics._worker.join(2)
        self.assertEqual(diagnostics.snapshot()['results']['smoke']['status'], 'error')
        self.assertEqual(len(opens), 1)

    def setUp(self):
        if not hasattr(socket, 'AF_UNIX'):
            self.skipTest('Unix-domain IPC tests run on the Linux device')
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = str(Path(self.folder.name) / 'screen.sock')
        self.now = 10.
        self.screen = FakeScreen()
        self.daemon = None
        self.proxy = RemoteScreenService(socket_path=self.path, clock=lambda: self.now)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.proxy.stop()
        if self.daemon:
            self.daemon.close()

    def start_daemon(self):
        self.daemon = ScreenDaemon(self.screen, self.path, clock=lambda: self.now)
        self.daemon.start()

    def cycle(self, count=12):
        events = []
        for _ in range(count):
            if self.daemon:
                self.daemon.step()
            events.extend(self.proxy.read_screen_events())
            self.now += .05
        return events

    def display(self, value):
        return self.proxy.handle_message({'scene': 'smoke', 'value': value,
                                          'status': 'normal', 'online': True})

    def test_absent_daemon_never_blocks_device_proxy_and_keeps_latest_words(self):
        started = time.monotonic()
        self.proxy.start()
        for value in range(100):
            self.display(value)
            self.proxy.read_screen_events()
        self.assertLess(time.monotonic() - started, 1)
        self.assertIsNone(self.proxy.channel)
        self.assertTrue(self.proxy.latest)
        self.assertLess(len(self.proxy.latest), 100)

    def test_daemon_restart_replays_latest_display_without_restarting_backend(self):
        self.start_daemon()
        self.proxy.start()
        self.display(347)
        self.cycle()
        expected = dict(self.proxy.latest)
        self.assertTrue(self.screen.frames)
        self.daemon.close()
        self.daemon = None
        self.cycle()
        self.display(402)
        self.start_daemon()
        self.now += 2
        self.screen.frames.clear()
        self.cycle(30)
        self.assertTrue(self.proxy.started)
        self.assertTrue(self.screen.frames)
        replay = {int.from_bytes(f[4:6], 'big'): int.from_bytes(f[6:8], 'big')
                  for f in self.screen.frames}
        self.assertEqual(replay, self.proxy.latest)
        self.assertNotEqual(expected, self.proxy.latest)

    def test_legacy_lock_words_use_phrase_font_without_backend_restart(self):
        self.start_daemon()
        self.proxy.start()
        for legacy, expected in (("正", "好"), ("异", "坏"), ("检", "检")):
            self.proxy.latest[0x6010] = int.from_bytes(legacy.encode('gb2312'), 'big')
            self.proxy.dirty.add(0x6010)
            self.cycle()
            self.assertEqual(self.screen.frames[-1],
                             bytes.fromhex('5aa505826010') + expected.encode('gb2312'))

    def test_screen_read_failure_reconnects_only_screen_and_resends_display(self):
        self.start_daemon()
        self.proxy.start()
        self.display(347)
        self.cycle()
        channel = self.proxy.channel
        self.screen.fail_read = True
        self.cycle(1)
        self.assertFalse(self.daemon.online)
        self.screen.fail_read = False
        self.screen.frames.clear()
        self.now += 3
        self.cycle()
        self.assertIs(channel, self.proxy.channel)
        self.assertTrue(self.daemon.online)
        self.assertTrue(self.screen.frames)

    def test_screen_write_failure_does_not_disconnect_backend(self):
        self.start_daemon()
        self.proxy.start()
        self.cycle()
        channel = self.proxy.channel
        self.screen.fail_write = True
        self.display(347)
        self.cycle(1)
        self.assertFalse(self.daemon.online)
        self.assertIs(channel, self.proxy.channel)
        self.screen.fail_write = False
        self.now += 3
        self.cycle()
        self.assertTrue(self.daemon.online)

    def test_missing_physical_screen_does_not_prevent_ipc_connection(self):
        self.screen.fail_open = True
        self.start_daemon()
        self.proxy.start()
        self.display(347)
        self.cycle()
        self.assertIsNotNone(self.proxy.channel)
        self.assertFalse(self.daemon.online)
        self.screen.fail_open = False
        self.now += 3
        self.cycle()
        self.assertTrue(self.screen.frames)

    def test_live_button_delivered_once_and_old_buttons_expire(self):
        self.start_daemon()
        self.proxy.start()
        self.cycle()
        event = {'event': 'diagnostics.sensors'}
        self.screen.events.append(event)
        self.assertEqual(self.cycle(), [event])
        self.daemon.channel.queue({'kind': 'event', 'event': event,
                                   'created_at': self.now - 3})
        self.daemon.channel.flush()
        self.assertEqual(self.cycle(), [])

    def test_buttons_without_backend_are_discarded(self):
        self.start_daemon()
        self.cycle(1)
        self.screen.events.append({'event': 'fan.start'})
        self.cycle(1)
        self.proxy.start()
        self.assertEqual(self.cycle(), [])

    def test_fragmented_ipc_message_and_buffer_limit(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        channel = JsonChannel(a)
        b.sendall(b'{"kind":')
        self.assertEqual(channel.receive(), [])
        b.sendall(b'"ready"}\n')
        self.assertEqual(channel.receive(), [{'kind': 'ready'}])
        with self.assertRaises(OSError):
            channel.queue({'value': 'x' * MAX_BUFFER})

    def test_daemon_rejects_non_write_serial_frames(self):
        self.start_daemon()
        self.proxy.start()
        self.cycle()
        self.proxy.channel.queue({'kind': 'write', 'frames': ['5aa50483000001']})
        self.proxy.channel.flush()
        self.daemon.step()
        self.assertIsNone(self.daemon.channel)
        self.assertEqual(self.screen.frames, [])


if __name__ == '__main__':
    unittest.main()
