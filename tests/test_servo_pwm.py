"""舵机硬件 PWM 的单位换算、启停、异常和控制器状态检测。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Sensor.resources import GpioResource
from JETSON.Sensor.actuators.servo import ServoDriver
from JETSON.Sensor.diagnostics.actuator_checks import ActuatorStatusChecker
from test_sensor_architecture import FakeGPIO


class PwmGPIO(FakeGPIO):
    def __init__(self, directory):
        super().__init__()
        self.directory = Path(directory)
        self.pwms = []

    def PWM(self, pin, frequency):
        module = self
        path = self.directory / 'pwm0'
        path.mkdir(exist_ok=True)

        class Pwm:
            def __init__(self):
                self._ch_info = SimpleNamespace(pwm_chip_dir=str(module.directory), pwm_id=0)
                self.fail = False
                self.calls = []
                for key, value in {'period': int(1e9 / frequency), 'duty_cycle': 0, 'enable': 0}.items():
                    (path / key).write_text(str(value))

            def start(self, duty):
                self.calls.append(duty)
                if self.fail:
                    raise OSError('PWM write failed')
                (path / 'duty_cycle').write_text(str(int(1e9 / frequency * duty / 100)))
                (path / 'enable').write_text('1')

            def stop(self):
                (path / 'enable').write_text('0')

            def ChangeDutyCycle(self, duty):
                raise AssertionError('简化版本应直接调用 PWM.start')

        pwm = Pwm()
        self.pwms.append(pwm)
        return pwm

    def cleanup(self, pins):
        if isinstance(pins, int):
            pins = (pins,)
        super().cleanup(pins)


class ServoPwmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.raw = PwmGPIO(self.temp.name)
        self.gpio = GpioResource(DEFAULT_CONFIG, module=self.raw)
        self.driver = ServoDriver(self.gpio, DEFAULT_CONFIG.servo)
        self.driver.start()

    def tearDown(self):
        self.driver.close()
        self.gpio.close()
        self.temp.cleanup()

    def test_startup_leaves_output_disabled(self):
        state = self.driver.snapshot()
        self.assertEqual(state['pwm_state'], {'period': 20000000, 'duty_cycle': 0, 'enable': 0})
        self.assertFalse(state['on'])
        self.assertEqual(self.raw.writes, [])

    def test_three_positions_have_correct_period_and_pulse_width(self):
        for angle, ns, duty in ((45, 1000000, 5), (90, 1500000, 7.5), (135, 2000000, 10)):
            with self.subTest(angle=angle):
                self.driver.set(angle)
                self.assertEqual(self.raw.pwms[0].calls[-1], duty)
                self.assertEqual(self.driver.snapshot()['pwm_state'],
                                 {'period': 20000000, 'duty_cycle': ns, 'enable': 1})

    def test_stop_then_start_reenables_controller(self):
        self.driver.set(90)
        self.driver.stop()
        self.assertEqual(self.driver.snapshot()['pwm_state']['enable'], 0)
        self.driver.set(45)
        self.assertEqual(self.driver.snapshot()['pwm_state']['enable'], 1)

    def test_invalid_angle_never_starts_output(self):
        for angle in (True, float('nan'), float('inf')):
            with self.subTest(angle=angle), self.assertRaises(ValueError):
                self.driver.set(angle)
        self.assertEqual(self.raw.pwms[0].calls, [])

    def test_failed_command_disables_controller_and_preserves_last_angle(self):
        self.driver.set(45)
        self.raw.pwms[0].fail = True
        with self.assertRaises(OSError):
            self.driver.set(90)
        state = self.driver.snapshot()
        self.assertEqual(state['angle'], 45)
        self.assertEqual(state['pwm_state']['enable'], 0)
        self.assertEqual(state['status'], 'error')

    def test_close_releases_pin_and_requires_new_start(self):
        self.driver.close()
        self.assertIn(15, self.raw.cleaned)
        with self.assertRaises(RuntimeError):
            self.driver.set(90)
        self.driver.start()
        self.driver.set(90)
        self.assertTrue(self.driver.snapshot()['on'])

    def test_diagnostics_reads_controller_without_requiring_python_thread(self):
        checker = ActuatorStatusChecker(SimpleNamespace(config=DEFAULT_CONFIG))
        self.driver.set(90)
        state = self.driver.snapshot()
        self.assertEqual(checker._check_driver('servo', state, True, '')['status'], 'normal')
        self.assertEqual(checker._check_signal('servo', state, True)['status'], 'normal')
        (Path(self.temp.name) / 'pwm0' / 'duty_cycle').write_text('100000')
        self.assertEqual(checker._check_signal('servo', self.driver.snapshot(), True)['status'], 'error')
        self.driver.stop()
        self.assertEqual(checker._check_signal('servo', self.driver.snapshot(), False)['status'], 'normal')

    def test_unavailable_controller_read_is_reported(self):
        (Path(self.temp.name) / 'pwm0' / 'period').unlink()
        state = self.driver.snapshot()
        self.assertTrue(state['pwm_read_error'])
        checker = ActuatorStatusChecker(SimpleNamespace(config=DEFAULT_CONFIG))
        self.assertEqual(checker._check_signal('servo', state, False)['status'], 'error')

    def test_sequence_holds_then_cycles_and_stop_prevents_later_moves(self):
        self.driver.close()
        cfg = replace(DEFAULT_CONFIG.servo, sweep_interval_seconds=4)
        self.driver = ServoDriver(self.gpio, cfg)
        self.driver.start()
        self.driver.set(sweep=True)
        self.driver._advance_sweep()
        self.assertEqual(self.driver.angle, 45)
        angles = [self.driver.angle]
        for _ in range(4):
            self.driver._next_move = 0
            self.driver._advance_sweep()
            angles.append(self.driver.angle)
        self.assertEqual(angles, [45, 135, 90, 45, 135])
        self.assertEqual(self.raw.pwms[-1].calls, [5, 10, 7.5, 5, 10])
        self.driver.stop()
        calls = len(self.raw.pwms[-1].calls)
        self.driver._advance_sweep()
        self.assertEqual(len(self.raw.pwms[-1].calls), calls)
        self.assertFalse(self.driver.snapshot()['sweeping'])

    def test_sweep_worker_failure_disables_motion(self):
        self.driver.close()
        self.driver = ServoDriver(self.gpio, replace(DEFAULT_CONFIG.servo, sweep_interval_seconds=5))
        self.driver.start()
        self.driver.set(sweep=True)
        self.raw.pwms[-1].fail = True
        self.driver._next_move = 0
        with self.assertRaises(OSError):
            self.driver._advance_sweep()
        self.assertFalse(self.driver.sweeping)
        self.assertFalse(self.driver.on)
        self.assertEqual(self.driver.snapshot()['pwm_state']['enable'], 0)


if __name__ == '__main__':
    unittest.main()
