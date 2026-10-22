"""Real FFAA parser/driver, freshness and original status VP, with fake UART."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock

from config import DEFAULT_CONFIG
from experiments.smoke.jetson.driver import SmokeDriver
from experiments.smoke.jetson.controller import Hardware
from experiments.smoke.jetson.diagnostics import check_smoke
from experiments.smoke.jetson.smoke_protocol import SmokeFrameParser
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser
from system.diagnostics.jetson.service import DiagnosticService
from system.diagnostics.bridge import DiagnosticsScreenBridge


class Uart:
    def __init__(self):
        self.data = bytearray()
        self.closed = False
    @property
    def in_waiting(self): return len(self.data)
    def read(self, count):
        result = bytes(self.data[:count])
        del self.data[:count]
        return result
    def close(self): self.closed = True


class SmokeStatusTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.uart = Uart()
        self.factory = Mock(return_value=self.uart)
        self.driver = SmokeDriver(DEFAULT_CONFIG.smoke, serial_factory=self.factory,
            clock=lambda: self.now, wall_clock=lambda: self.now + 1000)

    def manager(self):
        def poll(name):
            self.assertEqual(name, 'smoke')
            return self.driver.read()
        return SimpleNamespace(config=DEFAULT_CONFIG, _started=True, poll_sensor=Mock(side_effect=poll))

    def test_complete_frame_is_normal_and_concentration_is_not_a_health_threshold(self):
        for frame, value in [('ffaa00003c', 0), ('ffaa015b3c', 347), ('ffaaffff3c', 65535)]:
            self.uart.data.extend(bytes.fromhex(frame))
            result = check_smoke(self.manager())
            self.assertEqual(result['status'], 'normal')
            self.assertEqual(result['concentration'], value)
            self.assertEqual(result['unit'], 'raw')
        self.factory.assert_called_once()

    def test_split_frame_requires_both_parts_and_resynchronizes_after_noise(self):
        self.uart.data.extend(bytes.fromhex('1266ffaa01'))
        self.assertNotEqual(check_smoke(self.manager())['status'], 'normal')
        self.uart.data.extend(bytes.fromhex('5b3c'))
        result = check_smoke(self.manager())
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['concentration'], 347)
        self.assertEqual(result['valid_frames'], 1)

    def test_bad_tail_and_noise_cannot_be_accepted_as_valid_data(self):
        self.driver.start()
        self.now += DEFAULT_CONFIG.smoke.stale_after_seconds + .01
        self.uart.data.extend(bytes.fromhex('ffaa015b001122334455'))
        result = check_smoke(self.manager())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['valid_frames'], 0)
        self.assertGreater(result['tail_errors'], 0)

    def test_multiple_frames_keep_the_newest_complete_value(self):
        parser = SmokeFrameParser()
        self.assertEqual(parser.feed(bytes.fromhex('ffaa00013cffaa015b3c')), [1, 347])
        self.assertEqual(parser.last_frame, bytes.fromhex('ffaa015b3c'))

    def test_diagnostic_rejects_cached_frame_before_background_stale_timeout(self):
        self.uart.data.extend(bytes.fromhex('ffaa015b3c'))
        manager = self.manager()
        first = check_smoke(manager)
        self.now += 1
        recent = check_smoke(manager)
        self.assertEqual(recent['status'], 'error')
        self.assertFalse(recent['fresh_frame_received'])
        self.assertEqual(recent['sample_time'], first['sample_time'])
        self.now += DEFAULT_CONFIG.smoke.stale_after_seconds
        stale = check_smoke(manager)
        self.assertEqual(stale['status'], 'error')
        self.assertIsNone(stale['concentration'])
        self.assertIn('超时', stale['note'])
        self.factory.assert_called_once()

    def test_data_recovers_without_reopening_or_replugging_uart(self):
        self.driver.start()
        self.now += 6
        manager = self.manager()
        self.assertEqual(check_smoke(manager)['status'], 'error')
        self.uart.data.extend(bytes.fromhex('ffaa015b3c'))
        self.assertEqual(check_smoke(manager)['status'], 'normal')
        self.factory.assert_called_once()
        self.assertFalse(self.uart.closed)

    def test_serial_error_cannot_pass_diagnostics(self):
        self.uart.read = Mock(side_effect=OSError('UART read failed'))
        result = DiagnosticService(self.manager()).check('smoke')
        self.assertEqual(result['status'], 'error')
        self.assertIn('UART read failed', result['note'])
        self.assertTrue(self.uart.closed)

    def test_6002_text_does_not_overwrite_adjacent_results(self):
        memory = {0x6001: b'AA', 0x6003: b'BB'}
        for status, glyph in [('checking', '检'), ('normal', '好'), ('error', '坏')]:
            item = MessageParser().parse({'scene': 'diagnostics', 'statuses': {'smoke': status}})[0]
            self.assertEqual(item['address'], 0x6002)
            self.assertEqual(item['data'], glyph.encode('gb2312'))
            self.assertEqual(ScreenSender.build_write_bytes_frame(item['address'], item['data']),
                             bytes.fromhex('5aa505826002') + glyph.encode('gb2312'))
            memory[item['address']] = item['data']
        self.assertEqual(memory[0x6001], b'AA')
        self.assertEqual(memory[0x6003], b'BB')

    def test_one_manual_check_finishes_and_idle_ticks_do_not_repeat_sampling(self):
        self.uart.data.extend(bytes.fromhex('ffaa015b3c'))
        manager = self.manager()
        service = DiagnosticService(manager)
        screen = SimpleNamespace(handle_message=Mock(return_value=[]))
        bridge = DiagnosticsScreenBridge(screen, SimpleNamespace(
            request_diagnostics=service.request, diagnostic_snapshot=service.snapshot))
        self.assertTrue(bridge.handle_event('diagnostics.smoke')['handled'])
        service._worker.join(2)
        self.assertFalse(service.snapshot()['busy'])
        self.assertEqual(service.snapshot()['results']['smoke']['status'], 'normal')
        for _ in range(10): bridge.tick()
        manager.poll_sensor.assert_called_once_with('smoke')
        screen.handle_message.assert_called_once()
        service.stop()

    def test_auto_session_survives_diagnostic_and_recovers_on_new_frame(self):
        owner = SimpleNamespace(
            config=DEFAULT_CONFIG, sensors={}, _lock=threading.RLock(),
            _sampling_condition=threading.Condition(), _active_reads=0,
            _pintest_mode=False, _stop=threading.Event(), _init_errors=[],
        )
        opened = []

        def factory(**_kwargs):
            uart = Uart()
            uart.data.extend(bytes.fromhex('ffaa015b3c'))
            opened.append(uart)
            return uart

        driver = SmokeDriver(DEFAULT_CONFIG.smoke, serial_factory=factory)
        hardware = Hardware(owner, sensor_drivers={'smoke': driver})
        manager = SimpleNamespace(
            config=DEFAULT_CONFIG,
            acquire_sensor=lambda name, client: hardware.acquire_reader(name, client),
            release_sensor=lambda name, client: hardware.release_reader(name, client),
            poll_sensor=lambda name: hardware.poll_sensor(name),
        )

        hardware.initialize_readers()
        self.assertEqual(len(opened), 1)
        for attempt in (1, 2):
            if attempt == 2:
                self.assertEqual(check_smoke(manager)['status'], 'error')
                opened[-1].data.extend(bytes.fromhex('ffaa015b3c'))
            result = check_smoke(manager)
            self.assertEqual(result['status'], 'normal')
            self.assertEqual(len(opened), 1)
            self.assertFalse(opened[-1].closed)
            self.assertIsNotNone(driver.serial)
            self.assertEqual(hardware._reader_clients['smoke'], {'auto'})
        hardware.close_readers()

    def test_manual_check_reports_no_frame_without_waiting_for_later_frame(self):
        events = []
        states = iter([
            {'protocol': 'ffaa', 'status': 'unknown', 'note': '等待完整帧'},
            {'protocol': 'ffaa', 'status': 'error', 'note': '烟雾有效帧超时'},
            {'protocol': 'ffaa', 'status': 'normal', 'concentration': 347},
        ])
        smoke_config = replace(DEFAULT_CONFIG.smoke, diagnostic_timeout_seconds=6.0)
        config = replace(DEFAULT_CONFIG, smoke=smoke_config)

        def poll(name):
            events.append('poll')
            return next(states)

        manager = SimpleNamespace(
            config=config,
            acquire_sensor=lambda name, client: events.append('open'),
            release_sensor=lambda name, client: events.append('close'),
            poll_sensor=poll,
        )
        result = check_smoke(manager)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(events, ['open', 'poll', 'close'])

    def test_no_frame_does_not_reconnect_or_wait_in_manual_check(self):
        events = []
        recovered = {'value': False}
        smoke_config = replace(
            DEFAULT_CONFIG.smoke,
            diagnostic_timeout_seconds=1.0,
            diagnostic_reconnect_interval_seconds=0.05,
        )
        config = replace(DEFAULT_CONFIG, smoke=smoke_config)

        def poll(name):
            events.append('poll')
            if recovered['value']:
                return {'protocol': 'ffaa', 'status': 'normal', 'concentration': 412}
            return {'protocol': 'ffaa', 'status': 'unknown', 'note': '等待完整帧'}

        def reconnect(name, client):
            events.append('reopen')
            recovered['value'] = True

        manager = SimpleNamespace(
            config=config,
            acquire_sensor=lambda name, client: events.append('open'),
            release_sensor=lambda name, client: events.append('close'),
            reconnect_sensor=reconnect,
            poll_sensor=poll,
        )
        result = check_smoke(manager)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('reopen', events)
        self.assertEqual(events[-1], 'close')

    def test_blocked_driver_publishes_error_and_error_glyph_within_one_second(self):
        import time
        gate = threading.Event()
        manager = SimpleNamespace(config=DEFAULT_CONFIG, _started=True,
            poll_sensor=lambda name: (gate.wait(3), {'status': 'error'})[1])
        service = DiagnosticService(manager)
        screen = SimpleNamespace(handle_message=lambda message: MessageParser().parse(message))
        bridge = DiagnosticsScreenBridge(screen, SimpleNamespace(
            request_diagnostics=service.request, diagnostic_snapshot=service.snapshot))
        try:
            started = time.monotonic()
            bridge.handle_event('diagnostics.smoke')
            bridge.tick()
            service._worker.join(1.5)
            self.assertFalse(service.snapshot()['busy'])
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(service.snapshot()['results']['smoke']['status'], 'error')
            items = bridge.tick()
            smoke = next(item for item in items if item['address'] == 0x6002)
            self.assertEqual(smoke['data'], '坏'.encode('gb2312'))
        finally:
            gate.set()
            service.stop()

    def test_current_missing_or_invalid_payload_always_returns_error(self):
        for state in ({'protocol': 'ffaa', 'status': 'normal', 'concentration': None},
                      {'protocol': 'ffaa', 'status': 'normal', 'concentration': True},
                      {'protocol': 'ffaa', 'status': 'checking', 'concentration': None},
                      {'online': True, 'percent': None, 'decoder_configured': True}):
            with self.subTest(state=state):
                release = Mock()
                manager = SimpleNamespace(config=DEFAULT_CONFIG, acquire_sensor=Mock(),
                    release_sensor=release, poll_sensor=Mock(return_value=state))
                self.assertEqual(check_smoke(manager)['status'], 'error')
                release.assert_called_once_with('smoke', 'smoke-diagnostics')


if __name__ == '__main__': unittest.main()
