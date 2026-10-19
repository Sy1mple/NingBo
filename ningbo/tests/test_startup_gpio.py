"""真实驱动与模拟 GPIO：启动写关闭电平，按钮前不动作、不自动检测。"""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from application import NingBoApplication
from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from device_service import DeviceService
from hardware.screen.frames import ScreenEventParser
from screen_service import ScreenService


class GpioTrace:
    def __init__(self):
        self.calls, self.levels = [], {}
    def open(self): pass
    def close(self): pass
    def setup_output(self, pin, level=0):
        self.calls.append(('setup', pin, level))
        self.levels[pin] = level
    def write(self, pin, level):
        self.calls.append(('write', pin, level))
        self.levels[pin] = level
    def read(self, pin): return self.levels[pin]
    def create_pwm(self, pin, frequency):
        self.calls.append(('pwm_create', pin))
        state = {'period': round(1e9 / frequency), 'duty_cycle': 0, 'enable': 0}
        def start(duty):
            self.calls.append(('pwm_start', pin, duty))
            state.update(enable=1, duty_cycle=round(state['period'] * duty / 100))
        def stop():
            self.calls.append(('pwm_stop', pin))
            state['enable'] = 0
        return SimpleNamespace(start=start, stop=stop, state=state)
    def pwm_state(self, pwm): return pwm.state.copy()
    def release_pwm(self, pin): self.calls.append(('pwm_release', pin))


class Screen:
    def __init__(self): self.events, self.messages = [], []
    def start(self): pass
    def stop(self): pass
    def discard_startup_events(self): self.events.clear()
    def read_screen_events(self):
        records, self.events = self.events, []
        return records
    def handle_message(self, message):
        self.messages.append(message)
        return []


class StartupTests(unittest.TestCase):
    static_outputs = {'fan', 'lock', 'rgb', 'buzzer'}
    off_levels = {29: 0, 31: 0, 12: 0, 35: 0, 38: 0, 40: 1, 13: 0, 16: 0, 18: 0, 22: 0}

    def setUp(self):
        self.gpio = GpioTrace()
        self.config = replace(DEFAULT_CONFIG, current=replace(DEFAULT_CONFIG.current, enabled=False),
                              ads1115=replace(DEFAULT_CONFIG.ads1115, enabled=False))
        self.manager = DeviceManager(config=self.config, gpio=self.gpio, sensor_drivers={})
        self.devices = DeviceService(manager=self.manager)
        self.screen = Screen()
        self.app = NingBoApplication(config=self.config, devices=self.devices, screen=self.screen,
                                    access_handler=lambda event: None, voice_handler=lambda event: None)
        self.addCleanup(self.app.stop)

    def test_startup_sets_off_levels_and_100_idle_loops_do_not_activate_outputs(self):
        self.app.start()
        startup_calls = list(self.gpio.calls)
        self.assertEqual(self.gpio.levels, self.off_levels)
        self.assertEqual(len(startup_calls), 10)
        self.assertTrue(all(call[0] == 'setup' and call[2] == self.off_levels[call[1]]
                            for call in startup_calls))
        for _ in range(100):
            self.app.process_once()
        self.assertEqual(self.gpio.calls, startup_calls)
        self.assertEqual(self.manager._ready_outputs, self.static_outputs)
        self.assertIsNone(self.devices.diagnostics._worker)
        self.assertEqual(self.devices.diagnostic_snapshot()['phase'], 'idle')
        for name, state in self.manager.snapshot()['actuators'].items():
            if name in self.static_outputs:
                self.assertTrue(state['initialized'])
                self.assertFalse(state.get('on'))
            elif name in ('servo', 'stepper'):
                self.assertFalse(state['initialized'])
        for name in ('servo', 'stepper'):
            self.assertIsNone(self.manager._outputs[name]._thread)

    def test_diagnostics_require_button_and_never_start_uninitialized_outputs(self):
        self.app.start()
        startup_calls = list(self.gpio.calls)
        self.assertEqual(self.devices.diagnostic_snapshot()['revision'], 0)
        self.screen.events.append({'event': 'diagnostics.actuators'})
        self.app.process_once()
        self.devices.diagnostics._worker.join(2)
        report = self.devices.diagnostic_snapshot()
        self.assertEqual(report['phase'], 'completed')
        self.assertEqual(set(report['results']), {'servo', 'stepper', 'fan', 'lock', 'buzzer', 'rgb'})
        for name in ('servo', 'stepper'):
            self.assertEqual(report['results'][name]['status'], 'unknown')
        for name in self.static_outputs:
            self.assertFalse(report['results'][name]['cmd_on'])
            self.assertEqual(report['results'][name]['signal']['status'], 'normal')
        self.assertEqual(self.gpio.calls, startup_calls)
        self.assertEqual(self.manager._ready_outputs, self.static_outputs)

    def test_fan_button_switches_prepared_output_without_repeating_setup(self):
        self.app.start()
        startup_calls = list(self.gpio.calls)
        self.screen.events.append({'event': 'fan.start'})
        self.app.process_once()
        self.assertEqual(self.manager._ready_outputs, self.static_outputs)
        self.assertEqual(self.gpio.calls, startup_calls + [('write', self.config.fan.pin, 1)])
        self.screen.events.append({'event': 'fan.stop'})
        self.app.process_once()
        self.assertEqual(self.gpio.calls[-1], ('write', self.config.fan.pin, 0))
        self.assertEqual(sum(c[0] == 'setup' and c[1] == self.config.fan.pin for c in self.gpio.calls), 1)

    def test_manual_diagnostics_detect_single_rgb_channel_readback_mismatch(self):
        self.app.start()
        self.assertIsNone(self.devices.diagnostics._worker)
        self.gpio.levels[38] = 1
        self.screen.events.append({'event': 'diagnostics.actuators'})
        self.app.process_once()
        self.devices.diagnostics._worker.join(2)
        result = self.devices.diagnostic_snapshot()['results']['rgb']
        self.assertEqual(result['signal']['status'], 'error')
        self.assertEqual(result['signal']['expected'], {12: 0, 35: 0, 38: 0})
        self.assertEqual(result['signal']['observed'], {12: 0, 35: 0, 38: 1})

    def test_stop_without_any_control_keeps_static_outputs_off_and_motors_uninitialized(self):
        self.app.start()
        self.app.stop()
        self.assertEqual(self.gpio.levels, self.off_levels)
        self.assertTrue(all(call[0] in ('setup', 'write') and call[2] == self.off_levels[call[1]]
                            for call in self.gpio.calls))

    def test_startup_buffered_buttons_are_discarded(self):
        self.screen.events.extend([{'event': 'fan.start'}, {'event': 'diagnostics.all'}])
        self.app.start()
        self.app.process_once()
        self.assertEqual(self.gpio.levels, self.off_levels)
        self.assertEqual(len(self.gpio.calls), 10)
        self.assertIsNone(self.devices.diagnostics._worker)

    def test_explicit_servo_control_still_initializes_pwm(self):
        self.app.start()
        self.devices.control('servo', angle=90)
        self.assertEqual(self.manager._ready_outputs, self.static_outputs | {'servo'})
        self.assertTrue(any(c[0] == 'pwm_start' for c in self.gpio.calls))
        self.assertTrue(self.manager.snapshot()['actuators']['servo']['initialized'])

    def test_failed_initialization_is_not_retried_by_idle_loops(self):
        self.app.start()
        driver = self.manager._outputs['servo']
        driver.start = Mock(side_effect=RuntimeError('setup failed'))
        with self.assertRaisesRegex(RuntimeError, 'setup failed'):
            self.devices.control('servo', angle=90)
        for _ in range(10): self.app.process_once()
        with self.assertRaises(RuntimeError):
            self.devices.control('servo', angle=90)
        self.assertEqual(driver.start.call_count, 1)
        self.assertFalse(self.manager.snapshot()['actuators']['servo']['initialized'])

    def test_partial_rgb_startup_failure_cleans_prepared_outputs_and_never_starts_motors(self):
        driver = self.manager._outputs['rgb']
        def fail_after_first_pin():
            self.gpio.setup_output(self.config.rgb.pins[0], 0)
            raise OSError('RGB setup failed')
        driver.start = Mock(side_effect=fail_after_first_pin)
        with self.assertRaisesRegex(OSError, 'RGB setup failed'):
            self.app.start()
        self.assertFalse(self.manager._started)
        self.assertEqual(self.manager._ready_outputs, set())
        self.assertTrue(all(call[0] in ('setup', 'write') and call[2] == self.off_levels[call[1]]
                            for call in self.gpio.calls))
        for name in ('servo', 'stepper'):
            self.assertIsNone(self.manager._outputs[name]._thread)

    def test_repeated_start_does_not_repeat_static_setup(self):
        self.app.start()
        startup_calls = list(self.gpio.calls)
        self.app.start()
        self.devices.start()
        self.assertEqual(self.gpio.calls, startup_calls)

    def test_disabled_static_devices_are_not_initialized(self):
        config = replace(self.config, fan=replace(self.config.fan, enabled=False),
                         rgb=replace(self.config.rgb, enabled=False),
                         buzzer=replace(self.config.buzzer, enabled=False))
        gpio = GpioTrace()
        manager = DeviceManager(config=config, gpio=gpio, sensor_drivers={})
        self.addCleanup(manager.stop)
        manager.start()
        self.assertEqual(gpio.calls, [('setup', 29, 0)] + [('setup', pin, 0) for pin in config.stepper.pins])
        self.assertEqual(manager._ready_outputs, {'lock'})

    def test_startup_respects_each_configured_active_polarity(self):
        config = replace(self.config, lock=replace(self.config.lock, active_high=False),
                         fan=replace(self.config.fan, active_high=False),
                         rgb=replace(self.config.rgb, active_high=False),
                         buzzer=replace(self.config.buzzer, active_high=True))
        gpio = GpioTrace()
        manager = DeviceManager(config=config, gpio=gpio, sensor_drivers={})
        self.addCleanup(manager.stop)
        manager.start()
        self.assertEqual(gpio.levels, {29: 1, 31: 1, 12: 1, 35: 1, 38: 1, 40: 0, 13: 0, 16: 0, 18: 0, 22: 0})

    def test_startup_clears_previous_high_stepper_phases_without_worker(self):
        self.gpio.levels.update({pin: 1 for pin in self.config.stepper.pins})
        self.app.start()
        driver = self.manager._outputs['stepper']
        self.assertTrue(driver.snapshot()['pins_prepared'])
        self.assertFalse(driver.snapshot()['running'])
        self.assertIsNone(driver._thread)
        self.assertNotIn('stepper', self.manager._ready_outputs)
        self.assertEqual(self.gpio.levels, self.off_levels)

    def test_first_stepper_button_reuses_pins_and_stop_closes_all_phases(self):
        self.app.start()
        startup = list(self.gpio.calls)
        driver = self.manager._outputs['stepper']
        with patch('experiments.stepper.jetson.gpio.threading.Thread') as thread:
            self.devices.control('stepper', dir_='fwd', n=2)
            thread.return_value.start.assert_called_once()
            self.assertEqual(self.gpio.calls, startup)
            driver.step_once()
            self.assertTrue(any(self.gpio.levels[pin] for pin in self.config.stepper.pins))
            self.devices.stop_actuator('stepper')
            self.assertTrue(all(self.gpio.levels[pin] == 0 for pin in self.config.stepper.pins))
            thread.return_value.is_alive.return_value = False

    def test_restart_reclaims_stepper_pins_without_starting_worker(self):
        self.app.start()
        self.app.stop()
        self.gpio.levels.update({pin: 1 for pin in self.config.stepper.pins})
        self.app.start()
        self.assertEqual(self.gpio.levels, self.off_levels)
        self.assertIsNone(self.manager._outputs['stepper']._thread)

    def test_disabled_stepper_has_no_startup_pin_setup(self):
        config = replace(self.config, stepper=replace(self.config.stepper, enabled=False))
        gpio = GpioTrace()
        manager = DeviceManager(config=config, gpio=gpio, sensor_drivers={})
        self.addCleanup(manager.stop)
        manager.start()
        self.assertFalse(any(pin in gpio.levels for pin in config.stepper.pins))

    def test_partial_stepper_idle_setup_failure_cleans_only_prepared_pins(self):
        setup = self.gpio.setup_output
        def failing(pin, level=0):
            if pin == self.config.stepper.pins[1]:
                raise OSError('stepper setup failed')
            setup(pin, level)
        self.gpio.setup_output = failing
        with self.assertRaisesRegex(OSError, 'stepper setup failed'):
            self.app.start()
        self.assertFalse(self.manager._started)
        self.assertIsNone(self.manager._outputs['stepper']._thread)
        writes = [call for call in self.gpio.calls if call[0] == 'write' and call[1] in self.config.stepper.pins]
        self.assertEqual(writes, [('write', self.config.stepper.pins[0], 0)])

    def test_explicit_pin_test_has_cleanup_for_actuator_pins(self):
        self.app.start()
        # 本组测试关闭了真实采样；维护模式退出仍需恢复一个模拟的超声驱动。
        self.manager._sensor_owners['ultrasonic']._readers['ultrasonic'] = SimpleNamespace(start=Mock(), close=Mock())
        self.assertTrue(self.devices.pintest_on()['ok'])
        self.assertEqual(self.manager._ready_outputs, {'fan', 'lock', 'rgb', 'buzzer', 'servo', 'stepper'})
        self.devices.pintest_set(self.config.fan.pin, 1)
        self.devices.pintest_off()
        self.assertEqual(self.gpio.levels[self.config.fan.pin], 0)

    def test_screen_reset_discards_probe_bytes_and_partial_frame(self):
        screen = ScreenService()
        pending = Mock(return_value=b'buffered-startup-data')
        serial = SimpleNamespace(is_open=True, reset_input_buffer=Mock())
        screen.sender = SimpleNamespace(take_pending_input=pending, ser=serial)
        screen.event_parser.feed(b'\x5a\xa5')
        screen.discard_startup_events()
        pending.assert_called_once()
        serial.reset_input_buffer.assert_called_once()
        self.assertEqual(screen.event_parser._buffer, bytearray())


if __name__ == '__main__': unittest.main()
