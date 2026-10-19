"""ECHO timing failures, validity checks and original-VP text packets."""
from types import SimpleNamespace
from copy import deepcopy
import threading
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from experiments.ultrasonic.jetson.gpio import UltrasonicDriver
from experiments.ultrasonic.jetson.diagnostics import check_ultrasonic
from experiments.ultrasonic.jetson.controller import Hardware
from device_manager import DeviceManager
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser
from system.diagnostics.jetson.service import DiagnosticService
from system.diagnostics.bridge import DiagnosticsScreenBridge


class UltrasonicStatusTests(unittest.TestCase):
    def driver(self, levels):
        gpio = SimpleNamespace(read=Mock(side_effect=levels), write=Mock())
        return UltrasonicDriver(gpio, DEFAULT_CONFIG.ultrasonic), gpio

    def timed_read(self, levels, times, response_only=False):
        driver, gpio = self.driver(levels)
        with patch('experiments.ultrasonic.jetson.gpio.time.sleep'), \
                patch('experiments.ultrasonic.jetson.gpio.time.perf_counter', side_effect=times):
            value = driver.read_response() if response_only else driver.read()
        return value, gpio

    def test_valid_echo_produces_distance_and_trigger_pulse(self):
        value, gpio = self.timed_read([1, 1, 0], [0, .001, .0014, .002])
        self.assertEqual(value, {'distance': 17.2})
        self.assertEqual([c.args for c in gpio.write.call_args_list], [(32, 0), (32, 1), (32, 0)])

    def test_no_rising_echo_is_timeout(self):
        with self.assertRaisesRegex(TimeoutError, '未上升'):
            self.timed_read([0], [0, .031])

    def test_stuck_high_echo_is_timeout(self):
        with self.assertRaisesRegex(TimeoutError, '未下降'):
            self.timed_read([1, 1], [0, .001, .042])

    def test_too_short_echo_is_invalid(self):
        with self.assertRaisesRegex(ValueError, '脉宽'):
            self.timed_read([1, 1, 0], [0, .001, .00102, .00105])

    def test_distance_above_limit_is_invalid(self):
        with self.assertRaisesRegex(ValueError, '距离'):
            self.timed_read([1, 1, 0], [0, .001, .002, .031])

    def test_response_check_accepts_echo_outside_ranging_limits(self):
        for times in ([0, .001, .00102, .00105], [0, .001, .002, .031]):
            response, _ = self.timed_read([1, 1, 0], times, response_only=True)
            manager = SimpleNamespace(poll_sensor=Mock(return_value=response))
            self.assertEqual(check_ultrasonic(manager)['status'], 'normal')
            manager.poll_sensor.assert_called_once_with('ultrasonic', response_only=True)

    def test_diagnostic_requires_completed_response_not_a_distance_threshold(self):
        for pulse in (None, True, float('nan'), float('inf'), -1, 0):
            manager = SimpleNamespace(poll_sensor=Mock(return_value={
                'echo_received': True, 'echo_pulse_us': pulse, 'distance': 20}))
            with self.subTest(pulse=pulse):
                self.assertEqual(check_ultrasonic(manager)['status'], 'error')
        manager.poll_sensor.return_value = {'echo_received': False, 'echo_pulse_us': 1000}
        self.assertEqual(check_ultrasonic(manager)['status'], 'error')
        for distance in (None, 0, 401, 600):
            manager = SimpleNamespace(poll_sensor=Mock(return_value={
                'echo_received': True, 'echo_pulse_us': 1000, 'distance': distance}))
            self.assertEqual(check_ultrasonic(manager)['status'], 'normal')

    def test_original_driver_timeout_evidence_is_preserved(self):
        state = {'status': 'error', 'distance': None, 'note': '超声波 ECHO 未上升'}
        result = check_ultrasonic(SimpleNamespace(poll_sensor=Mock(return_value=state)))
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['note'], state['note'])

    def test_each_status_writes_one_word_at_6004_without_touching_6005(self):
        for status, glyph in [('checking', '检'), ('normal', '好'), ('error', '坏')]:
            item = MessageParser().parse({'scene': 'diagnostics', 'statuses': {'ultrasonic': status}})[0]
            self.assertEqual(item['address'], 0x6004)
            self.assertEqual(item['data'], glyph.encode('gb2312'))
            frame = ScreenSender.build_write_bytes_frame(item['address'], item['data'])
            self.assertEqual(frame, bytes.fromhex('5aa505826004') + glyph.encode('gb2312'))

    def test_one_button_request_finishes_and_retains_result_without_rechecking(self):
        manager = SimpleNamespace(config=DEFAULT_CONFIG, _started=True,
            poll_sensor=Mock(return_value={'echo_received': True, 'echo_pulse_us': 1000}))
        diagnostic = DiagnosticService(manager)
        screen = SimpleNamespace(handle_message=Mock(return_value=[]))
        bridge = DiagnosticsScreenBridge(screen, SimpleNamespace(
            request_diagnostics=diagnostic.request, diagnostic_snapshot=diagnostic.snapshot))
        self.assertTrue(bridge.handle_event('diagnostics.ultrasonic')['handled'])
        diagnostic._worker.join(2)
        self.assertFalse(diagnostic._worker.is_alive())
        report = diagnostic.snapshot()
        self.assertEqual(report['phase'], 'completed')
        self.assertEqual(report['results']['ultrasonic']['status'], 'normal')
        bridge.tick()
        for _ in range(10): bridge.tick()
        manager.poll_sensor.assert_called_once_with('ultrasonic', response_only=True)
        self.assertEqual(screen.handle_message.call_count, 1)
        diagnostic.stop()

    def test_response_poll_uses_driver_lock_and_does_not_replace_ranging_cache(self):
        driver, gpio = self.driver([1, 1, 0])
        cached = {'distance': 20, 'status': 'normal', 'sample_time': 123}
        owner = SimpleNamespace(sensors={'ultrasonic': deepcopy(cached)},
            _sampling_condition=threading.Condition(), _active_reads=0,
            _pintest_mode=False, _stop=threading.Event(), _lock=threading.RLock())
        scene = object.__new__(Hardware)
        scene.owner, scene._readers = owner, {'ultrasonic': driver}
        manager = object.__new__(DeviceManager)
        manager._sensor_owners = {'ultrasonic': scene}
        with patch('experiments.ultrasonic.jetson.gpio.time.sleep'), \
                patch('experiments.ultrasonic.jetson.gpio.time.perf_counter',
                      side_effect=[0, .001, .002, .031]):
            result = check_ultrasonic(manager)
        self.assertEqual(result['status'], 'normal')
        self.assertGreater(result['distance'], 400)
        self.assertEqual(owner.sensors['ultrasonic'], cached)
        self.assertEqual(owner._active_reads, 0)
        self.assertFalse(driver.lock.locked())
        owner._pintest_mode = True
        gpio.read.reset_mock()
        self.assertEqual(check_ultrasonic(manager)['status'], 'error')
        gpio.read.assert_not_called()

    def test_response_check_timeouts_remain_errors(self):
        for levels, times in (([0], [0, .031]), ([1, 1], [0, .001, .042])):
            with self.assertRaises(TimeoutError):
                self.timed_read(levels, times, response_only=True)


if __name__ == '__main__': unittest.main()
