from dataclasses import replace
import unittest
from unittest.mock import patch
from JETSON.Sensor.Config import Ads1115Config, DEFAULT_CONFIG
from JETSON.Sensor.resources import I2cResource
from JETSON.Sensor.sensors.ads1115 import Ads1115Monitor
from JETSON.Sensor.diagnostics.sensor_checks import SensorStatusChecker
from JETSON.Sensor.diagnostics.actuator_checks import ActuatorStatusChecker

class Bus:
    ready = True
    raw = -1200
    bad_config = False
    short = False
    fail_address = None
    def write_i2c_block_data(self, address, register, data):
        self.last_address = address
        if address == self.fail_address:
            raise OSError('ADC did not acknowledge')
        self.config = (data[0] << 8) | data[1]
    def read_i2c_block_data(self, address, register, count):
        if self.short:
            return [0]
        value = self.config if register == 1 else self.raw & 0xffff
        if register == 1:
            if not self.ready:
                value &= 0x7fff
            if self.bad_config:
                value ^= 0x1000
        return [value >> 8, value & 255]

class AdsTests(unittest.TestCase):
    def setUp(self):
        self.bus = Bus()
        self.i2c = I2cResource(8, factory=lambda number: self.bus)
        self.monitor = Ads1115Monitor(self.i2c, Ads1115Config())
    def test_signed_voltage_and_channel_selection(self):
        result = self.monitor.read_channel(2)
        self.assertAlmostEqual(result['volts'], -0.15)
        self.assertEqual((self.bus.config >> 12) & 7, 6)
        self.assertEqual((self.bus.config >> 9) & 7, 1)
    def test_busy_conversion_times_out(self):
        self.bus.ready = False
        with patch('JETSON.Sensor.sensors.ads1115.time.monotonic', side_effect=[0, 1]):
            with self.assertRaises(TimeoutError):
                self.monitor.read_channel(0)
    def test_config_mismatch_and_short_read_fail(self):
        self.bus.bad_config = True
        with self.assertRaises(IOError):
            self.monitor.read_channel(0)
        self.bus.short = True
        with self.assertRaises(IOError):
            self.monitor.read_channel(0)
    def test_all_channels_and_saturation(self):
        self.bus.raw = 32767
        result = self.monitor.read_all()
        self.assertEqual([v['channel'] for v in result['readings']], [0, 1, 2, 3])
        self.assertTrue(all(v['saturated'] for v in result['readings']))
    def test_voltage_does_not_prove_actuator_health(self):
        class Manager:
            config = DEFAULT_CONFIG
            adc_monitor = self.monitor
        result = SensorStatusChecker(Manager()).check('current')
        self.assertEqual(result['status'], 'unconfigured')
        self.assertEqual(result['adc_status'], 'normal')
        self.assertEqual(len(result['readings']), 4)
    def test_disabled_and_invalid_config(self):
        with self.assertRaises(ValueError):
            replace(DEFAULT_CONFIG, ads1115=Ads1115Config(timeout_seconds=0)).validate()
        with self.assertRaises(RuntimeError):
            Ads1115Monitor(self.i2c, Ads1115Config(enabled=False)).read_all()

    def test_servo_a0_feedback_stays_unknown_until_calibrated(self):
        class Manager:
            config = replace(DEFAULT_CONFIG, ads1115=replace(DEFAULT_CONFIG.ads1115, feedback_voltage_limits={}))
            adc_monitor = self.monitor
        checker = ActuatorStatusChecker(Manager())
        result = checker._check_current('servo', True, None)
        self.assertEqual(result['channel'], 0)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['type'], 'adc_voltage')
        self.assertNotIn('current_ma', result)
        self.bus.raw = 32767
        self.assertEqual(checker._check_current('servo', True, None)['status'], 'error')

    def test_servo_voltage_limits_match_command_and_handle_ambiguity(self):
        class Manager:
            config = DEFAULT_CONFIG
            adc_monitor = self.monitor
        checker = ActuatorStatusChecker(Manager())
        self.bus.raw = 104  # 13 mV
        self.assertEqual(checker._check_current('servo', True, None)['status'], 'normal')
        self.assertEqual(checker._check_current('servo', False, None)['status'], 'error')
        self.bus.raw = 69  # 8.625 mV
        self.assertEqual(checker._check_current('servo', False, None)['status'], 'idle')
        self.assertEqual(checker._check_current('servo', True, None)['status'], 'error')
        self.bus.raw = 86  # 10.75 mV: ambiguous
        self.assertEqual(checker._check_current('servo', True, None)['status'], 'unknown')

    def test_buzzer_uses_second_adc_and_reports_missing_chip(self):
        class Manager:
            config = DEFAULT_CONFIG
            adc_monitor = self.monitor
        checker = ActuatorStatusChecker(Manager())
        r = checker._check_current('buzzer', True, None)
        self.assertEqual(self.bus.last_address, 0x49)
        self.assertEqual(r['address'], '0x49')
        self.assertEqual(r['channel'], 0)
        self.assertEqual(r['status'], 'unknown')
        self.bus.fail_address = 0x49
        r = checker._check_current('buzzer', False, None)
        self.assertEqual(r['status'], 'error')
        self.assertEqual(r['samples_volts'], [])
        self.assertIn('0x49/AIN0', r['note'])
        self.assertEqual(checker._check_current('servo', False, None)['address'], '0x48')
