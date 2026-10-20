"""A3 electrical response, cleanup, cancellation and fixed-VP text packets."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from hardware.rgb.diagnostics import RgbStatusCheck
from hardware.rgb.gpio import RgbDriver
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser


class RgbStatusTests(unittest.TestCase):
    def manager(self, previous='off'):
        state = {'initialized': True, 'color': previous, 'on': previous != 'off'}
        manager = SimpleNamespace(config=DEFAULT_CONFIG, _control_lock=threading.RLock(),
            _pintest_mode=False, _outputs={'rgb': object()},
            actuator_state=Mock(side_effect=lambda name: dict(state)),
            record_actuator_diagnostic=Mock(), stop_actuator=Mock())

        def control(name, color):
            self.assertEqual(name, 'rgb')
            state.update(color=color, on=color != 'off')

        manager.control = Mock(side_effect=control)
        manager.actuator_output_levels = Mock(side_effect=lambda name: {
            pin: value for pin, value in zip(DEFAULT_CONFIG.rgb.pins, RgbDriver.COLORS[state['color']])})
        readings = {'off': -.00054, 'red': -.00027, 'green': -.00034, 'blue': -.00039}
        manager.adc_monitor = SimpleNamespace(read_channel=Mock(side_effect=lambda *a, **kw: {
            'volts': readings[state['color']], 'saturated': False}))
        return manager, readings

    def check(self, manager, cancel=None):
        with patch('hardware.rgb.diagnostics.time.sleep'):
            return RgbStatusCheck(manager).check('rgb', cancel)

    def test_all_three_colors_pass_with_negative_offset_and_previous_color_restored(self):
        manager, _ = self.manager('yellow')
        result = self.check(manager)
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['channel'], 3)
        self.assertEqual(result['address'], '0x48')
        self.assertEqual(set(result['colors']), {'red', 'green', 'blue'})
        self.assertEqual(manager.actuator_state('rgb')['color'], 'yellow')
        manager.adc_monitor.read_channel.assert_called_with(3, address=0x48, full_scale_volts=.256)
        self.assertEqual(manager.adc_monitor.read_channel.call_count, 81)

    def test_missing_blue_response_is_error_even_if_red_and_green_work(self):
        manager, readings = self.manager()
        readings['blue'] = readings['off']
        result = self.check(manager)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['colors']['blue']['status'], 'error')
        self.assertEqual(result['colors']['red']['status'], 'normal')
        self.assertEqual(manager.actuator_state('rgb')['color'], 'off')

    def test_unstable_off_baseline_cannot_pass_as_a_color_response(self):
        manager, _ = self.manager()
        values = ([.0] * 9 + [.0006] * 9 + [.0002] * 9) * 3
        manager.adc_monitor.read_channel.side_effect = [
            {'volts': value, 'saturated': False} for value in values]
        result = self.check(manager)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(all(item['baseline_drift_volts'] > .00006
                            for item in result['colors'].values()))

    def test_gpio_mismatch_and_adc_failures_do_not_pass_and_restore_color(self):
        for failure in ('gpio', 'saturation', 'nan', 'i2c'):
            with self.subTest(failure=failure):
                manager, _ = self.manager('green')
                if failure == 'gpio':
                    manager.actuator_output_levels.return_value = {}
                    manager.actuator_output_levels.side_effect = None
                elif failure == 'i2c':
                    manager.adc_monitor.read_channel.side_effect = OSError('I2C failure')
                else:
                    manager.adc_monitor.read_channel.side_effect = None
                    manager.adc_monitor.read_channel.return_value = {
                        'volts': float('nan') if failure == 'nan' else .256,
                        'saturated': failure == 'saturation'}
                self.assertEqual(self.check(manager)['status'], 'error')
                self.assertEqual(manager.actuator_state('rgb')['color'], 'green')

    def test_cancel_before_detection_does_not_switch_the_light(self):
        manager, _ = self.manager()
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(self.check(manager, cancel)['status'], 'unknown')
        manager.control.assert_not_called()
        manager.adc_monitor.read_channel.assert_not_called()

    def test_cancel_during_detection_restores_previous_color(self):
        manager, _ = self.manager('red')
        cancel = threading.Event()

        def sample(*args, **kwargs):
            cancel.set()
            return {'volts': -.00054, 'saturated': False}

        manager.adc_monitor.read_channel.side_effect = sample
        self.assertEqual(self.check(manager, cancel)['status'], 'unknown')
        self.assertEqual(manager.actuator_state('rgb')['color'], 'red')

    def test_restore_failure_reports_error_and_attempts_shutdown(self):
        manager, _ = self.manager('yellow')
        change = manager.control.side_effect

        def control(name, color):
            if color == 'yellow':
                raise OSError('restore failed')
            return change(name, color)

        manager.control.side_effect = control
        self.assertEqual(self.check(manager)['status'], 'error')
        manager.stop_actuator.assert_called_once_with('rgb')

    def test_unconfigured_adc_never_lights_the_led(self):
        manager, _ = self.manager()
        manager.config = replace(DEFAULT_CONFIG,
            ads1115=replace(DEFAULT_CONFIG.ads1115, enabled=False))
        self.assertEqual(self.check(manager)['status'], 'unconfigured')
        manager.control.assert_not_called()

    def test_fixed_6008_receives_exactly_one_word_without_overwriting_6009(self):
        for status, glyph in [('checking', '检'), ('normal', '好'), ('error', '坏')]:
            item = MessageParser().parse({'scene': 'diagnostics', 'statuses': {'rgb': status}})[0]
            self.assertEqual(item['address'], 0x6008)
            self.assertEqual(item['data'], glyph.encode('gb2312'))
            frame = ScreenSender.build_write_bytes_frame(item['address'], item['data'])
            self.assertEqual(frame, bytes.fromhex('5aa505826008') + glyph.encode('gb2312'))


if __name__ == '__main__': unittest.main()
