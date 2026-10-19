"""Real buzzer polarity/driver with fake GPIO and A4 conversion data."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from experiments.ultrasonic.jetson.buzzer_gpio import BuzzerDriver
from experiments.ultrasonic.jetson.diagnostics import BuzzerStatusCheck
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser


class Gpio:
    def __init__(self): self.levels, self.calls = {}, []
    def open(self): pass
    def close(self): pass
    def setup_output(self, pin, level=0):
        self.levels[pin] = level; self.calls.append(('setup', pin, level))
    def write(self, pin, level):
        self.levels[pin] = level; self.calls.append(('write', pin, level))
    def read(self, pin): return self.levels[pin]


class BuzzerStatusTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG, buzzer=replace(DEFAULT_CONFIG.buzzer,
            diagnostic_settle_seconds=.001))
        self.gpio = Gpio()
        self.driver = BuzzerDriver(self.gpio, self.config.buzzer)
        self.manager = DeviceManager(config=self.config, gpio=self.gpio,
            sensor_drivers={}, output_drivers={'buzzer': self.driver})
        self.manager.start(); self.addCleanup(self.manager.stop)
        self.volts = {False: -.00115, True: .0165}
        self.manager.adc_monitor = SimpleNamespace(read_channel=Mock(side_effect=lambda *a, **kw: {
            'volts': self.volts[self.driver.on], 'saturated': False}))
        patcher = patch('experiments.ultrasonic.jetson.diagnostics.time.sleep')
        patcher.start(); self.addCleanup(patcher.stop)

    def check(self, event=None):
        return BuzzerStatusCheck(self.manager).check('buzzer', event)

    def test_boot_is_high_and_silent_then_manual_test_uses_low_for_sound(self):
        self.assertEqual(self.gpio.levels, {40: 1})
        calls = list(self.gpio.calls)
        for _ in range(50): self.manager.snapshot()
        self.assertEqual(self.gpio.calls, calls)
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['before']['levels'], {40: 1})
        self.assertEqual(result['running']['levels'], {40: 0})
        self.assertEqual(result['after']['levels'], {40: 1})
        self.assertFalse(self.driver.on)
        self.assertEqual(result['restored_levels'], {40: 1})
        self.manager.adc_monitor.read_channel.assert_called_with(0, address=0x49, full_scale_volts=.256)
        self.assertEqual(self.manager.adc_monitor.read_channel.call_count, 45)

    def test_low_run_feedback_is_error(self):
        self.volts[True] = -.00115
        self.assertEqual(self.check()['status'], 'error')
        self.assertEqual(self.gpio.levels, {40: 1})

    def test_constant_high_adc_cannot_pass_stop_and_delta_checks(self):
        self.volts[False] = .0165
        self.assertEqual(self.check()['status'], 'error')

    def test_off_baseline_drift_cannot_pass(self):
        values = [-.001] * 15 + [.0165] * 15 + [.003] * 15
        self.manager.adc_monitor.read_channel.side_effect = [
            {'volts': value, 'saturated': False} for value in values]
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertGreater(result['baseline_drift_volts'], .002)

    def test_gpio_readback_mismatch_is_error_and_final_output_is_off(self):
        self.gpio.read = lambda pin: 1 - self.gpio.levels[pin]
        self.assertEqual(self.check()['status'], 'error')
        self.assertEqual(self.gpio.levels, {40: 1})

    def test_adc_errors_saturation_and_nonfinite_values_do_not_pass(self):
        for sample in (OSError('I2C failed'), {'volts': .256, 'saturated': True},
                       {'volts': float('nan'), 'saturated': False}):
            with self.subTest(sample=sample):
                reader = self.manager.adc_monitor.read_channel
                reader.side_effect = sample if isinstance(sample, Exception) else None
                if isinstance(sample, dict): reader.return_value = sample
                self.assertEqual(self.check()['status'], 'error')
                self.assertEqual(self.gpio.levels, {40: 1})

    def test_cancel_before_request_does_not_switch_the_pin(self):
        event = threading.Event(); event.set()
        calls = list(self.gpio.calls)
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertEqual(self.gpio.calls, calls)

    def test_cancellation_while_sounding_restores_high_off_level(self):
        event = threading.Event()
        original = self.manager.adc_monitor.read_channel.side_effect
        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.driver.on: event.set()
            return result
        self.manager.adc_monitor.read_channel.side_effect = read
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertEqual(self.gpio.levels, {40: 1})
        self.assertFalse(self.driver.on)

    def test_existing_on_state_is_restored(self):
        self.manager.control('buzzer', on=True)
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertTrue(result['restored_on'])
        self.assertTrue(self.driver.on)
        self.assertEqual(self.gpio.levels, {40: 0})

    def test_restore_failure_attempts_shutdown_and_reports_error(self):
        original = self.driver.set
        count = [0]
        def change(on):
            count[0] += 1
            if count[0] == 4: raise OSError('restore failed')
            return original(on)
        self.driver.set = change
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertIn('恢复', result['note'])
        self.assertEqual(self.gpio.levels, {40: 1})

    def test_polarity_comes_from_config(self):
        self.driver.config = replace(self.config.buzzer, active_high=True)
        self.manager.config = replace(self.config, buzzer=self.driver.config)
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['running']['levels'], {40: 1})
        self.assertEqual(result['restored_levels'], {40: 0})

    def test_missing_adc_configuration_does_not_sound_buzzer(self):
        self.manager.config = replace(self.config,
            ads1115=replace(self.config.ads1115, enabled=False))
        calls = list(self.gpio.calls)
        self.assertEqual(self.check()['status'], 'unconfigured')
        self.assertEqual(self.gpio.calls, calls)

    def test_fixed_6007_text_fits_one_word_and_does_not_touch_rgb_6008(self):
        for status, glyph in [('checking', '检'), ('normal', '好'), ('error', '坏')]:
            item = MessageParser().parse({'scene': 'diagnostics', 'statuses': {'buzzer': status}})[0]
            self.assertEqual(item['address'], 0x6007)
            self.assertEqual(ScreenSender.build_write_bytes_frame(item['address'], item['data']),
                             bytes.fromhex('5aa505826007') + glyph.encode('gb2312'))


if __name__ == '__main__': unittest.main()
