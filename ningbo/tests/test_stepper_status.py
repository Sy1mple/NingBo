"""A5 feedback with the real phase driver and deterministic thread scheduling."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from experiments.stepper.jetson.gpio import StepperDriver
from experiments.stepper.jetson.diagnostics import StepperStatusCheck
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser


class Gpio:
    def __init__(self): self.levels, self.calls = {}, []
    def open(self): pass
    def close(self): pass
    def setup_output(self, pin, level=0):
        self.levels[pin] = level; self.calls.append(('setup', pin, level))
    def write(self, pin, value):
        self.levels[pin] = value; self.calls.append(('write', pin, value))
    def read(self, pin): return self.levels[pin]


class StepperStatusTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG, stepper=replace(DEFAULT_CONFIG.stepper,
            diagnostic_settle_seconds=.001))
        self.gpio = Gpio()
        self.driver = StepperDriver(self.gpio, self.config.stepper)
        self.worker_alive = True

        def start():
            if len(self.driver._prepared_pins) != 4: self.driver.prepare_idle()
            self.driver._closing.clear()
            self.driver._thread = SimpleNamespace(join=lambda timeout: None,
                is_alive=lambda: self.worker_alive and not self.driver._closing.is_set())

        # Advance the real phase sequence once per ADC conversion. No wall-clock
        # thread is needed to verify counter changes and remaining-job semantics.
        self.driver.start = start
        self.manager = DeviceManager(config=self.config, gpio=self.gpio,
            sensor_drivers={}, output_drivers={'stepper': self.driver})
        self.manager.start()
        self.addCleanup(self.manager.stop)
        self.values = {'stop': -.0011, 'fwd': .240, 'rev': .245}
        self.advance = True

        def read(*args, **kwargs):
            direction = self.driver.direction
            if self.advance: self.driver.step_once()
            return {'volts': self.values[direction], 'saturated': False}

        self.manager.adc_monitor = SimpleNamespace(read_channel=Mock(side_effect=read))
        patcher = patch('experiments.stepper.jetson.diagnostics.time.sleep')
        sleep = patcher.start(); self.addCleanup(patcher.stop)
        def wait(seconds):
            if seconds == .05 and self.advance:
                for _ in range(50):
                    self.driver.step_once()
        sleep.side_effect = wait

    def check(self, event=None):
        return StepperStatusCheck(self.manager).check('stepper', event)

    def test_boot_keeps_phases_low_and_manual_check_runs_both_directions_then_stops(self):
        calls = list(self.gpio.calls)
        for _ in range(50): self.manager.snapshot()
        self.assertEqual(self.gpio.calls, calls)
        self.assertIsNone(self.driver._thread)
        self.assertTrue(all(value == 0 for value in self.gpio.levels.values()))
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual([s['direction'] for s in result['runs']], ['fwd', 'rev'])
        self.assertTrue(all(s['progressed_steps'] == 25 for s in result['runs']))
        self.assertTrue(all(s['completed_steps'] == 500 for s in result['runs']))
        self.assertEqual(result['restored_mode'], 'off')
        self.assertFalse(self.driver.snapshot()['running'])
        self.assertTrue(all(value == 0 for value in self.gpio.levels.values()))
        self.manager.adc_monitor.read_channel.assert_called_with(1, address=0x49, full_scale_volts=.512)
        self.assertEqual(self.manager.adc_monitor.read_channel.call_count, 125)

    def test_one_failed_direction_cannot_pass_overall_check(self):
        self.values['rev'] = -.0011
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['runs'][0]['status'], 'normal')
        self.assertEqual(result['runs'][1]['status'], 'error')
        self.assertFalse(self.driver.snapshot()['running'])

    def test_observed_moving_motor_feedback_passes_current_reference(self):
        self.values.update({'stop': .000031, 'fwd': .016672, 'rev': .016203})
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['minimum_delta_volts'], .008)
        self.assertTrue(all(s['completed_steps'] == 500 for s in result['runs']))

    def test_sampling_does_not_stop_motor_before_all_commanded_steps_finish(self):
        original = self.manager.stop_actuator
        stopped_remaining = []
        def stop(name):
            stopped_remaining.append(self.driver.remaining)
            return original(name)
        self.manager.stop_actuator = stop
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertTrue(all(n == 0 for n in stopped_remaining), stopped_remaining)

    def test_isolated_response_spikes_do_not_pass(self):
        original = self.manager.adc_monitor.read_channel.side_effect
        count = [0]
        def read(*args, **kwargs):
            sample = original(*args, **kwargs)
            if self.driver.direction != 'stop':
                count[0] += 1
                sample['volts'] = .020 if count[0] % 5 == 0 else .000031
            return sample
        self.manager.adc_monitor.read_channel.side_effect = read
        self.assertEqual(self.check()['status'], 'error')

    def test_cancel_during_remaining_motion_stops_phases(self):
        event = threading.Event()
        with patch.object(StepperStatusCheck, '_finish_direction', side_effect=InterruptedError('cancelled')):
            result = self.check(event)
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(self.driver.snapshot()['running'])
        self.assertTrue(all(v == 0 for v in self.gpio.levels.values()))

    def test_remaining_motion_detects_stalled_thread_and_bounds_wait(self):
        self.manager.control('stepper', dir_='fwd', n=500, spd=10)
        self.advance = False
        checker = StepperStatusCheck(self.manager)
        with patch('experiments.stepper.jetson.diagnostics.time.monotonic', side_effect=[0., 0., .6]):
            with self.assertRaisesRegex(RuntimeError, '持续未推进'):
                checker._finish_direction('fwd', 0., None)

    def test_invalid_diagnostic_speed_and_excessive_duration_are_rejected(self):
        for changes in ({'diagnostic_speed_ms': True}, {'diagnostic_speed_ms': 1},
                        {'diagnostic_steps': 1000}, {'diagnostic_speed_ms': 20}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.config, stepper=replace(self.config.stepper, **changes)).validate()

    def test_constant_high_adc_cannot_pass_off_or_delta_checks(self):
        self.values['stop'] = .240
        self.assertEqual(self.check()['status'], 'error')

    def test_alive_but_stalled_worker_is_not_normal(self):
        self.advance = False
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertIn('未推进', result['note'])
        self.assertTrue(all(value == 0 for value in self.gpio.levels.values()))

    def test_dead_worker_is_not_normal(self):
        self.worker_alive = False
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertIn('线程未运行', result['note'])

    def test_running_job_is_observed_without_restarting_or_overwriting_its_steps(self):
        self.manager.control('stepper', dir_='fwd', n=500, spd=8)
        with patch.object(self.manager, 'control', wraps=self.manager.control) as control, \
                patch.object(self.manager, 'stop_actuator', wraps=self.manager.stop_actuator) as stop:
            result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['mode'], 'current_run')
        self.assertEqual(self.driver.remaining, 475)
        self.assertEqual(self.driver.direction, 'fwd')
        control.assert_not_called(); stop.assert_not_called()

    def test_job_that_finishes_during_observation_is_not_resumed_or_repeated(self):
        self.manager.control('stepper', dir_='fwd', n=3, spd=8)
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['mode'], 'idle_bidirectional_test')
        self.assertEqual(self.driver.remaining, 0)
        self.assertEqual(self.driver.direction, 'stop')

    def test_cancel_before_detection_does_not_start_phase_thread(self):
        event = threading.Event(); event.set()
        calls = list(self.gpio.calls)
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertIsNone(self.driver._thread)
        self.assertEqual(self.gpio.calls, calls)

    def test_cancel_during_sampling_stops_all_phases(self):
        event = threading.Event()
        original = self.manager.adc_monitor.read_channel.side_effect
        def read(*args, **kwargs):
            value = original(*args, **kwargs); event.set(); return value
        self.manager.adc_monitor.read_channel.side_effect = read
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertFalse(self.driver.snapshot()['running'])
        self.assertTrue(all(value == 0 for value in self.gpio.levels.values()))

    def test_adc_errors_and_invalid_readings_cannot_pass(self):
        for sample in (OSError('I2C failed'), {'volts': .512, 'saturated': True},
                       {'volts': float('nan'), 'saturated': False}):
            with self.subTest(sample=sample):
                reader = self.manager.adc_monitor.read_channel
                reader.side_effect = sample if isinstance(sample, Exception) else None
                if isinstance(sample, dict): reader.return_value = sample
                self.assertEqual(self.check()['status'], 'error')
                self.assertTrue(all(value == 0 for value in self.gpio.levels.values()))

    def test_stop_gpio_mismatch_cannot_pass(self):
        self.gpio.read = lambda pin: int(pin == 13)
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertIn('低电平', result['note'])

    def test_missing_adc_config_does_not_start_motor(self):
        self.manager.config = replace(self.config,
            ads1115=replace(self.config.ads1115, enabled=False))
        self.assertEqual(self.check()['status'], 'unknown')
        self.assertIsNone(self.driver._thread)

    def test_fixed_6006_text_is_one_word_and_does_not_overwrite_6007(self):
        for status, glyph in [('checking', '检'), ('normal', '好'), ('error', '坏')]:
            item = MessageParser().parse({'scene': 'diagnostics', 'statuses': {'stepper': status}})[0]
            self.assertEqual(item['address'], 0x6006)
            self.assertEqual(ScreenSender.build_write_bytes_frame(item['address'], item['data']),
                             bytes.fromhex('5aa505826006') + glyph.encode('gb2312'))


if __name__ == '__main__': unittest.main()
