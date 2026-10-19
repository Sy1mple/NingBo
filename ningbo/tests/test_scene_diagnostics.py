"""Scene diagnostics: invalid inputs, output failures and shared-page compatibility."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock

from config import DEFAULT_CONFIG
from system.diagnostics.jetson.sensor_checks import SensorStatusChecker
from system.diagnostics.jetson.actuator_checks import ActuatorStatusChecker
from system.diagnostics.jetson.service import DiagnosticService
from system.diagnostics.serial_screen.addresses import STATUS_RESULT_ADDRESS_MAP, STATUS_CHECK_BINDINGS


class SceneDiagnosticsTests(unittest.TestCase):
    def manager(self, name, state):
        config = replace(DEFAULT_CONFIG,
            current=replace(DEFAULT_CONFIG.current, enabled=False),
            ads1115=replace(DEFAULT_CONFIG.ads1115, enabled=False))
        return SimpleNamespace(config=config, _started=True, _control_lock=threading.RLock(),
            _outputs={name: object()}, _readers={name: object()},
            poll_sensor=Mock(return_value=state), actuator_state=Mock(return_value=state),
            actuator_output_levels=Mock(), record_actuator_diagnostic=Mock())

    def test_sensor_ranges_reject_bool_nan_and_out_of_range(self):
        cases = [('dht11', {'temp': True, 'humidity': 50}),
                 ('dht11', {'temp': 25, 'humidity': 101}),
                 ('bh1750', {'lux': float('nan')}), ('bh1750', {'lux': -1}),
                 ('ultrasonic', {'distance': -1}),
                 ('body_temperature', {'temperature': float('inf')})]
        for name, state in cases:
            with self.subTest(name=name, state=state):
                self.assertEqual(SensorStatusChecker(self.manager(name, state)).check(name)['status'], 'error')

    def test_sensor_error_and_disabled_keep_original_evidence(self):
        for status in ('error', 'disabled'):
            state = {'status': status, 'note': 'driver evidence', 'errno': 121}
            self.assertEqual(SensorStatusChecker(self.manager('bh1750', state)).check('bh1750'), state)

    def test_smoke_ffaa_checks_raw_concentration_without_assuming_percent(self):
        for value, expected in [(0, 'normal'), (65535, 'normal'), (True, 'error'), (65536, 'error')]:
            state = {'status': 'normal', 'protocol': 'ffaa', 'concentration': value}
            with self.subTest(value=value):
                self.assertEqual(SensorStatusChecker(self.manager('smoke', state)).check('smoke')['status'], expected)

    def test_smoke_bytes_without_decoder_are_unconfigured(self):
        state = {'online': True, 'decoder_configured': False}
        self.assertEqual(SensorStatusChecker(self.manager('smoke', state)).check('smoke')['status'], 'unconfigured')

    def test_uninitialized_servo_does_not_read_outputs_or_feedback(self):
        manager = self.manager('servo', {'initialized': False})
        result = ActuatorStatusChecker(manager).check('servo')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['check_scope'], 'not_initialized')
        manager.actuator_output_levels.assert_not_called()

    def test_servo_pwm_mismatch_is_error(self):
        state = dict(initialized=True, on=True, backend='hardware_pwm', pwm_ready=True,
                     period_ns=20000000, pulse_us=1500,
                     pwm_state={'enable': 1, 'period': 20000000, 'duty_cycle': 500000})
        result = ActuatorStatusChecker(self.manager('servo', state)).check('servo')
        self.assertEqual(result['signal']['status'], 'error')
        self.assertEqual(result['status'], 'error')

    def test_running_stepper_requires_live_worker(self):
        manager = self.manager('stepper', dict(initialized=True, running=True, worker_alive=False))
        result = ActuatorStatusChecker(manager).check('stepper')
        self.assertEqual(result['driver']['status'], 'error')
        self.assertEqual(result['status'], 'error')
        manager.actuator_output_levels.assert_not_called()

    def test_buzzer_uses_its_active_low_polarity(self):
        manager = self.manager('buzzer', dict(initialized=True, on=False))
        manager.actuator_output_levels.return_value = {manager.config.buzzer.pin: 1}
        result = ActuatorStatusChecker(manager).check('buzzer')
        self.assertEqual(result['signal']['status'], 'normal')
        self.assertEqual(result['signal']['expected'], {40: 1})

    def test_adc_cancellation_returns_unknown_before_reading(self):
        manager = self.manager('lock', dict(initialized=True, on=True))
        manager.config = replace(manager.config, ads1115=replace(DEFAULT_CONFIG.ads1115,
            enabled=True, feedback_channels={'lock': 0}))
        manager.adc_monitor = Mock()
        manager.actuator_output_levels.return_value = {manager.config.lock.pin: 1}
        cancel = threading.Event()
        cancel.set()
        result = ActuatorStatusChecker(manager).check('lock', cancel_event=cancel)
        self.assertEqual(result['feedback']['status'], 'unknown')
        manager.adc_monitor.read_channel.assert_not_called()

    def test_alias_and_async_sensor_request_keep_report_contract(self):
        manager = self.manager('bh1750', {'lux': 0})
        service = DiagnosticService(manager)
        self.assertEqual(service.check('light')['name'], 'bh1750')
        self.assertTrue(service.request(target='light')['accepted'])
        service._worker.join(2)
        report = service.snapshot()
        self.assertFalse(report['busy'])
        self.assertEqual(report['phase'], 'completed')
        self.assertEqual(report['progress'], 100)
        self.assertEqual(report['results']['bh1750']['status'], 'normal')
        service.stop()

    def test_environment_text_addresses_and_existing_buttons(self):
        self.assertEqual(STATUS_RESULT_ADDRESS_MAP, dict(dht11=0x6000, bh1750=0x6001,
            smoke=0x6002, body_temperature=0x6003, ultrasonic=0x6004, servo=0x6005,
            stepper=0x6006, buzzer=0x6007, rgb=0x6008, fan=0x6009, lock=0x6010))
        self.assertEqual(STATUS_CHECK_BINDINGS, {'diagnostics.all': (0x9000, 0x8000),
            'diagnostics.sensors': (0x9001, 0x8001), 'diagnostics.actuators': (0x9002, 0x8002)})


if __name__ == '__main__':
    unittest.main()
