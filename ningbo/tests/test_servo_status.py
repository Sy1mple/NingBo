"""Manual-only A0 diagnostics with real servo driver and fake PWM/ADC hardware."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from experiments.servo.jetson.gpio import ServoDriver
from experiments.servo.jetson.diagnostics import ServoStatusCheck
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser


class PwmGpio:
    def __init__(self): self.calls = []
    def open(self): pass
    def close(self): pass
    def create_pwm(self, pin, frequency):
        self.calls.append(('create', pin))
        state = {'enable': 0, 'period': round(1e9 / frequency), 'duty_cycle': 0}
        def start(duty):
            state.update(enable=1, duty_cycle=round(state['period'] * duty / 100))
            self.calls.append(('start', duty))
        def stop():
            state['enable'] = 0
            self.calls.append(('stop',))
        return SimpleNamespace(start=start, stop=stop, state=state)
    def pwm_state(self, pwm): return dict(pwm.state)
    def release_pwm(self, pin): self.calls.append(('release', pin))


class ServoStatusTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG, servo=replace(DEFAULT_CONFIG.servo,
            diagnostic_settle_seconds=.001))
        self.gpio = PwmGpio()
        self.driver = ServoDriver(self.gpio, self.config.servo)
        self.manager = DeviceManager(config=self.config, gpio=self.gpio,
            sensor_drivers={}, output_drivers={'servo': self.driver})
        self.manager.start()
        self.addCleanup(self.manager.stop)
        self.values = {'off': .0085, 45: .0129, 90: .0130, 135: .0128}
        self.manager.adc_monitor = SimpleNamespace(read_channel=Mock(side_effect=lambda *a, **kw: {
            'volts': self.values[self.driver.angle if self.driver.on else 'off'], 'saturated': False}))
        sleep = patch('experiments.servo.jetson.diagnostics.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def check(self, cancel=None):
        return ServoStatusCheck(self.manager).check('servo', cancel)

    def test_startup_and_snapshots_do_not_initialize_pwm_but_manual_check_does(self):
        for _ in range(50): self.manager.snapshot()
        self.assertFalse(self.manager.actuator_state('servo')['initialized'])
        self.assertEqual(self.gpio.calls, [])
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual([p['angle'] for p in result['positions']], [45, 90, 135])
        self.assertEqual(result['restored_mode'], 'off')
        self.assertFalse(self.driver.on)
        self.assertEqual(sum(c[0] == 'create' for c in self.gpio.calls), 1)
        self.manager.adc_monitor.read_channel.assert_called_with(0, address=0x48, full_scale_volts=.256)
        self.assertEqual(self.manager.adc_monitor.read_channel.call_count, 45)

    def test_missing_run_feedback_is_error_and_pwm_is_stopped(self):
        self.values[90] = .0085
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['positions'][1]['status'], 'error')
        self.assertFalse(self.driver.on)

    def test_stuck_high_feedback_cannot_pass_stopped_or_delta_checks(self):
        self.values['off'] = .013
        self.assertEqual(self.check()['status'], 'error')
        self.assertFalse(self.driver.on)

    def test_excessive_baseline_drift_cannot_pass_even_if_absolute_limits_pass(self):
        values = [.0085] * 9 + [.013] * 27 + [.0095] * 9
        self.manager.adc_monitor.read_channel.side_effect = [
            {'volts': value, 'saturated': False} for value in values]
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertGreater(result['baseline_drift_volts'], .0005)

    def test_pwm_configuration_mismatch_is_error(self):
        original = self.gpio.pwm_state
        def observed(pwm):
            state = original(pwm)
            if state['enable']: state['duty_cycle'] = 0
            return state
        self.gpio.pwm_state = observed
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertIn('PWM', result['note'])
        self.assertFalse(self.driver.on)

    def test_adc_failure_and_saturation_are_errors_and_leave_pwm_off(self):
        for failure in (OSError('I2C failed'), {'volts': .256, 'saturated': True},
                        {'volts': float('nan'), 'saturated': False}):
            with self.subTest(failure=failure):
                self.manager.adc_monitor.read_channel.side_effect = failure if isinstance(failure, Exception) else None
                if isinstance(failure, dict): self.manager.adc_monitor.read_channel.return_value = failure
                self.assertEqual(self.check()['status'], 'error')
                self.assertFalse(self.driver.on)

    def test_cancel_before_check_never_allocates_pwm(self):
        event = threading.Event(); event.set()
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertEqual(self.gpio.calls, [])

    def test_cancel_during_sample_stops_and_restores_off_state(self):
        event = threading.Event()
        def read(*args, **kwargs):
            event.set()
            return {'volts': .0085, 'saturated': False}
        self.manager.adc_monitor.read_channel.side_effect = read
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertFalse(self.driver.on)

    def test_previous_holding_position_and_sweep_mode_are_restored(self):
        self.manager.control('servo', angle=80)
        self.assertEqual(self.check()['restored_mode'], 'hold')
        self.assertEqual(self.driver.angle, 80)
        self.assertTrue(self.driver.on)
        self.manager.control('servo', sweep=True)
        self.assertEqual(self.check()['restored_mode'], 'sweep')
        self.assertTrue(self.driver.sweeping)

    def test_missing_adc_configuration_never_initializes_a_stopped_servo(self):
        self.manager.config = replace(self.config,
            ads1115=replace(self.config.ads1115, enabled=False))
        self.assertEqual(self.check()['status'], 'unknown')
        self.assertEqual(self.gpio.calls, [])

    def test_restore_failure_reports_error_and_attempts_pwm_shutdown(self):
        self.manager.control('servo', angle=80)
        change = self.driver.set
        def control(angle=None, **kw):
            if angle == 80: raise OSError('restore failed')
            return change(angle=angle, **kw)
        self.driver.set = control
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertIn('恢复', result['note'])
        self.assertFalse(self.driver.on)

    def test_fixed_6005_text_does_not_touch_stepper_result_6006(self):
        for status, glyph in [('checking', '检'), ('normal', '好'), ('error', '坏')]:
            item = MessageParser().parse({'scene': 'diagnostics', 'statuses': {'servo': status}})[0]
            self.assertEqual(item['address'], 0x6005)
            self.assertEqual(ScreenSender.build_write_bytes_frame(item['address'], item['data']),
                             bytes.fromhex('5aa505826005') + glyph.encode('gb2312'))


if __name__ == '__main__': unittest.main()
