"""测距报警的按钮、有效读数、故障关闭与 GPIO 极性验证。"""
from dataclasses import replace
import unittest
from unittest.mock import patch

from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Sensor.actuators.outputs import SwitchDriver
from JETSON.Sensor.resources import GpioResource
from Serial_Screen.device_bridge import ScreenDeviceBridge
from business.proximity_alarm import ProximityAlarm
from test_device_bridge import FakeDevice, MemoryScreen
from test_sensor_architecture import FakeGPIO


class ProximityAlarmTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.devices = FakeDevice({"ultrasonic": {
            "distance": 3.0, "status": "normal", "sample_time": self.now,
        }})
        self.bridge = ScreenDeviceBridge(MemoryScreen(), self.devices,
                                         wall_clock=lambda: self.now)

    def buzzer_calls(self):
        return [args["on"] for name, args in self.devices.calls if name == "buzzer"]

    def test_button_near_boundary_stop_and_deduplication(self):
        self.devices.sensors["ultrasonic"]["distance"] = 3.0
        self.bridge.tick()
        self.assertEqual(self.buzzer_calls(), [])
        self.bridge.handle_event("ultrasonic.start")
        self.assertEqual(self.buzzer_calls(), [True])
        self.bridge.tick()
        self.assertEqual(self.buzzer_calls(), [True])
        self.devices.sensors["ultrasonic"]["distance"] = 5.0
        self.bridge.tick()
        self.assertEqual(self.buzzer_calls(), [True, False])
        self.devices.sensors["ultrasonic"]["distance"] = 4.9
        self.bridge.tick()
        self.bridge.handle_event("ultrasonic.stop")
        self.assertEqual(self.buzzer_calls(), [True, False, True, False])
        self.bridge.tick()
        self.assertEqual(len(self.buzzer_calls()), 4)

    def test_bad_and_stale_readings_turn_alarm_off(self):
        state = self.devices.sensors["ultrasonic"]
        for bad in (None, True, float("nan"), -1, 401):
            state.update(distance=3, status="normal", sample_time=self.now)
            self.bridge.handle_event("ultrasonic.start")
            state["distance"] = bad
            self.bridge.tick()
            self.assertFalse(self.buzzer_calls()[-1])
            self.bridge.handle_event("ultrasonic.stop")
        state.update(distance=3, status="normal", sample_time=self.now)
        self.bridge.handle_event("ultrasonic.start")
        self.bridge.tick()
        self.assertTrue(self.buzzer_calls()[-1])
        self.now += 6
        self.bridge.tick()
        self.assertFalse(self.buzzer_calls()[-1])
        state.update(sample_time=self.now, status="error")
        self.bridge.tick()
        self.assertFalse(self.buzzer_calls()[-1])

    def test_other_red_source_does_not_sound_alarm(self):
        self.bridge.handle_event("access.start")
        self.bridge.feedback.recognition(False)
        self.bridge.tick()
        self.assertEqual(self.buzzer_calls(), [])

    def test_rgb_disabled_does_not_disable_buzzer(self):
        config = replace(DEFAULT_CONFIG, rgb=replace(DEFAULT_CONFIG.rgb, enabled=False))
        bridge = ScreenDeviceBridge(MemoryScreen(), self.devices, config=config,
                                    wall_clock=lambda: self.now)
        self.devices.sensors["ultrasonic"]["distance"] = 3.0
        bridge.handle_event("ultrasonic.start")
        self.assertEqual(self.buzzer_calls(), [True])

    def test_failed_stop_retries_and_shutdown_closes(self):
        self.bridge.handle_event("ultrasonic.start")
        original = self.devices.control

        def fail_buzzer(name, **kwargs):
            if name == "buzzer":
                raise OSError("buzzer GPIO failed")
            return original(name, **kwargs)

        with patch.object(self.devices, "control", side_effect=fail_buzzer):
            with self.assertLogs("Serial_Screen.device_bridge", level="ERROR"):
                self.bridge.handle_event("ultrasonic.stop")
        self.bridge.tick()
        self.assertEqual(self.buzzer_calls(), [True, False])
        self.bridge.handle_event("ultrasonic.start")
        self.bridge.stop()
        self.assertEqual(self.buzzer_calls()[-2:], [True, False])

    def test_disabled_alarm_never_sounds(self):
        config = replace(DEFAULT_CONFIG, ultrasonic=replace(
            DEFAULT_CONFIG.ultrasonic, near_buzzer_enabled=False))
        self.devices.sensors["ultrasonic"]["distance"] = 3.0
        alarm = ProximityAlarm(self.devices, config, wall_clock=lambda: self.now)
        alarm.activate()
        alarm.observe(self.devices.sensors)
        alarm.flush()
        self.assertEqual(self.buzzer_calls(), [False])

    def test_real_switch_driver_uses_board40_and_polarity(self):
        for active_high in (True, False):
            config = replace(DEFAULT_CONFIG, buzzer=replace(
                DEFAULT_CONFIG.buzzer, active_high=active_high))
            raw = FakeGPIO()
            gpio = GpioResource(config, module=raw)
            gpio.open()
            driver = SwitchDriver(gpio, config.buzzer)
            driver.start()

            class Devices:
                def control(self, name, **kwargs):
                    if name != "buzzer":
                        raise AssertionError(name)
                    return driver.set(**kwargs)

            self.devices.sensors["ultrasonic"]["distance"] = 3.0
            alarm = ProximityAlarm(Devices(), config, wall_clock=lambda: self.now)
            alarm.activate()
            alarm.observe(self.devices.sensors)
            alarm.flush()
            self.assertEqual(raw.levels[40], int(active_high))
            alarm.release()
            alarm.flush()
            self.assertEqual(raw.levels[40], int(not active_high))
            gpio.close()


if __name__ == "__main__":
    unittest.main()
