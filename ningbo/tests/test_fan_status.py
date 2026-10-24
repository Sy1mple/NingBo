"""Real fan polarity/driver with fake GPIO and A2 conversion data."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from experiments.fan.jetson.gpio import FanDriver
from experiments.fan.jetson.diagnostics import FanStatusCheck
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


class FanStatusTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG, fan=replace(DEFAULT_CONFIG.fan,
            diagnostic_on_settle_seconds=.001, diagnostic_off_settle_seconds=.001))
        self.gpio = Gpio()
        self.driver = FanDriver(self.gpio, self.config.fan)
        self.manager = DeviceManager(config=self.config, gpio=self.gpio,
            sensor_drivers={}, output_drivers={'fan': self.driver})
        self.manager.start(); self.addCleanup(self.manager.stop)
        self.volts = {False: -.000531, True: .100}
        self.manager.adc_monitor = SimpleNamespace(read_channel=Mock(side_effect=lambda *a, **kw: {
            'volts': self.volts[self.driver.on], 'saturated': False}))
        patcher = patch('experiments.fan.jetson.diagnostics.time.sleep')
        patcher.start(); self.addCleanup(patcher.stop)

    def check(self, event=None):
        return FanStatusCheck(self.manager).check('fan', event)

    def test_boot_is_low_off_then_manual_test_uses_high_on(self):
        self.assertEqual(self.gpio.levels, {31: 0})
        calls = list(self.gpio.calls)
        for _ in range(50): self.manager.snapshot()
        self.assertEqual(self.gpio.calls, calls)
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['before']['levels'], {31: 0})
        self.assertEqual(result['running']['levels'], {31: 1})
        self.assertEqual(result['after']['levels'], {31: 0})
        self.assertFalse(self.driver.on)
        self.assertEqual(result['restored_levels'], {31: 0})
        self.manager.adc_monitor.read_channel.assert_called_with(2, address=0x48, full_scale_volts=.256)
        self.assertEqual(self.manager.adc_monitor.read_channel.call_count, 90)
        self.assertEqual(result['passed_rounds'], 2)

    def test_low_run_feedback_is_error(self):
        self.volts[True] = -.000531
        self.assertEqual(self.check()['status'], 'error')
        self.assertEqual(self.gpio.levels, {31: 0})

    def test_constant_high_adc_cannot_pass_stop_and_delta_checks(self):
        self.volts[False] = .100
        self.assertEqual(self.check()['status'], 'error')

    def test_off_baseline_drift_cannot_pass(self):
        values = ([-.001] * 15 + [.100] * 15 + [.008] * 15) * 2
        self.manager.adc_monitor.read_channel.side_effect = [
            {'volts': value, 'saturated': False} for value in values]
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertGreater(result['baseline_drift_volts'], .005)

    def test_gpio_readback_mismatch_is_error_and_final_output_is_off(self):
        self.gpio.read = lambda pin: 1 - self.gpio.levels[pin]
        self.assertEqual(self.check()['status'], 'error')
        self.assertEqual(self.gpio.levels, {31: 0})

    def test_adc_errors_saturation_and_nonfinite_values_do_not_pass(self):
        for sample in (OSError('I2C failed'), {'volts': .256, 'saturated': True},
                       {'volts': float('nan'), 'saturated': False}):
            with self.subTest(sample=sample):
                reader = self.manager.adc_monitor.read_channel
                reader.side_effect = sample if isinstance(sample, Exception) else None
                if isinstance(sample, dict): reader.return_value = sample
                self.assertEqual(self.check()['status'], 'error')
                self.assertEqual(self.gpio.levels, {31: 0})

    def test_cancel_before_request_does_not_switch_the_pin(self):
        event = threading.Event(); event.set()
        calls = list(self.gpio.calls)
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertEqual(self.gpio.calls, calls)

    def test_cancellation_while_running_restores_low_off_level(self):
        event = threading.Event()
        original = self.manager.adc_monitor.read_channel.side_effect
        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.driver.on: event.set()
            return result
        self.manager.adc_monitor.read_channel.side_effect = read
        self.assertEqual(self.check(event)['status'], 'unknown')
        self.assertEqual(self.gpio.levels, {31: 0})
        self.assertFalse(self.driver.on)

    def test_existing_on_state_is_restored(self):
        self.manager.control('fan', on=True)
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertTrue(result['restored_on'])
        self.assertTrue(self.driver.on)
        self.assertEqual(self.gpio.levels, {31: 1})

    def test_restore_failure_attempts_shutdown_and_reports_error(self):
        original = self.driver.set
        count = [0]
        def change(on):
            count[0] += 1
            if count[0] == 7: raise OSError('restore failed')
            return original(on)
        self.driver.set = change
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertIn('恢复', result['note'])
        self.assertEqual(self.gpio.levels, {31: 0})

    def test_polarity_comes_from_config(self):
        self.driver.config = replace(self.config.fan, active_high=False)
        self.manager.config = replace(self.config, fan=self.driver.config)
        result = self.check()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['running']['levels'], {31: 0})
        self.assertEqual(result['restored_levels'], {31: 1})

    def test_observed_running_fan_feedback_passes_without_old_absolute_limit(self):
        for value in (.041539, .042070):
            with self.subTest(value=value):
                self.volts[True] = value
                result = self.check()
                self.assertEqual(result['status'], 'normal')
                self.assertEqual(result['passed_rounds'], 2)

    def test_noisy_stopped_signal_raises_response_threshold(self):
        off = [-.001, 0, .001] * 5
        values = (off + [.002] * 15 + off) * 2
        self.manager.adc_monitor.read_channel.side_effect = [
            {'volts': value, 'saturated': False} for value in values]
        result = self.check()
        self.assertEqual(result['status'], 'error')
        self.assertGreater(result['rounds'][0]['minimum_delta_volts'], .002)

    def test_startup_spike_without_sustained_feedback_does_not_pass(self):
        off = [-.000531] * 15
        values = (off + [.100] * 3 + [-.000531] * 12 + off) * 2
        self.manager.adc_monitor.read_channel.side_effect = [
            {'volts': value, 'saturated': False} for value in values]
        self.assertEqual(self.check()['status'], 'error')

    def test_response_must_exceed_both_stopped_baselines(self):
        values = ([0.] * 15 + [.00052] * 15 + [.0005] * 15) * 2
        self.manager.adc_monitor.read_channel.side_effect = [
            {'volts': value, 'saturated': False} for value in values]
        self.assertEqual(self.check()['status'], 'error')

    def test_one_failed_round_can_recover_but_one_success_is_not_enough(self):
        off = [-.000531] * 15
        failure = off * 3
        success = off + [.042] * 15 + off
        for values, status in ((failure + success * 2, 'normal'),
                               (success + failure * 2, 'error')):
            with self.subTest(status=status):
                self.manager.adc_monitor.read_channel.side_effect = [
                    {'volts': value, 'saturated': False} for value in values]
                self.assertEqual(self.check()['status'], status)

    def test_invalid_noise_setting_is_rejected(self):
        for value in (0, float('nan'), 30):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.config, fan=replace(self.config.fan,
                    diagnostic_noise_multiplier=value)).validate()

    def test_missing_adc_configuration_does_not_start_fan(self):
        self.manager.config = replace(self.config,
            ads1115=replace(self.config.ads1115, enabled=False))
        calls = list(self.gpio.calls)
        self.assertEqual(self.check()['status'], 'unconfigured')
        self.assertEqual(self.gpio.calls, calls)

    def test_fixed_6009_text_fits_one_word_and_does_not_touch_lock_6010(self):
        for status, glyph in [('checking', '检'), ('normal', '好'), ('error', '坏')]:
            item = MessageParser().parse({'scene': 'diagnostics', 'statuses': {'fan': status}})[0]
            self.assertEqual(item['address'], 0x6009)
            self.assertEqual(ScreenSender.build_write_bytes_frame(item['address'], item['data']),
                             bytes.fromhex('5aa505826009') + glyph.encode('gb2312'))


if __name__ == '__main__': unittest.main()
