"""真实业务链路验证：屏幕帧、场景规则、设备 API、驱动输出和识别回调。"""
from dataclasses import replace
import unittest
from unittest.mock import patch

from JETSON.Sensor import DEFAULT_CONFIG, DeviceService
from JETSON.Sensor.manager import DeviceManager
from JETSON.Sensor.resources import GpioResource
from Serial_Screen.device_bridge import ScreenDeviceBridge
from Serial_Screen.diagnostics_bridge import DiagnosticsScreenBridge
from Serial_Screen.screen_service.screen.screen_events import ScreenEventParser
from test_device_bridge import FakeDevice, MemoryScreen
from test_sensor_architecture import FakeGPIO, fan_only_config


class DeviceFixture(FakeDevice):
    def stop_actuator(self, name):
        self.calls.append((name, {"stop": True}))
        return {"on": False}


class FeedbackBridgeTests(unittest.TestCase):
    def setUp(self):
        self.wall = [100.0]
        self.screen = MemoryScreen()
        self.devices = DeviceFixture({
            "body_temperature": {"temperature": 36.8, "status": "normal", "sample_time": 100.0},
            "ultrasonic": {"distance": 60.0, "status": "normal", "sample_time": 100.0},
        })
        self.bridge = ScreenDeviceBridge(self.screen, self.devices, clock=lambda: self.wall[0],
                                         wall_clock=lambda: self.wall[0])

    def color(self):
        return [args["color"] for name, args in self.devices.calls if name == "rgb"][-1]

    def dispatch(self, raw):
        return [self.bridge.handle_event(event) for event in ScreenEventParser().feed(bytes.fromhex(raw))]

    def test_no_button_means_no_business_display_or_rgb(self):
        self.bridge.tick()
        self.assertEqual(self.devices.calls, [])
        self.assertEqual(self.screen.frames, [])

    def test_body_start_normal_high_and_stop_from_screen_frames(self):
        self.dispatch("5A A5 06 83 20 30 01 40 30")
        self.assertEqual(self.color(), "green")
        self.assertEqual(self.screen.frames[-1], bytes.fromhex("5A A5 05 82 10 30 0E 60"))
        self.devices.sensors["body_temperature"]["temperature"] = 38.1
        self.wall[0] += 1
        self.bridge.tick()
        self.assertEqual(self.color(), "red")
        self.dispatch("5A A5 06 83 20 40 01 40 40")
        self.assertEqual(self.color(), "off")
        self.assertEqual(self.screen.frames[-1], bytes.fromhex("5A A5 05 82 10 30 00 00"))
        calls = len(self.devices.calls)
        self.wall[0] += 1
        self.bridge.tick()
        self.assertEqual(len(self.devices.calls), calls)

    def test_body_boundary_and_bad_reads_are_never_green(self):
        self.bridge.handle_event("body_temperature.start")
        state = self.devices.sensors["body_temperature"]
        state["temperature"] = 37.5
        self.bridge.tick()
        self.assertEqual(self.color(), "green")
        for bad in (None, True, float("nan"), float("inf"), 0, -1):
            with self.subTest(value=bad):
                state["temperature"] = bad
                self.bridge.tick()
                self.assertEqual(self.color(), "blue")
        state.update(temperature=36.8, status="error")
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")

    def test_far_near_and_exact_distance_boundary(self):
        self.dispatch("5A A5 06 83 20 50 01 40 50")
        self.assertEqual(self.color(), "green")
        self.devices.sensors["ultrasonic"]["distance"] = 4.9
        self.bridge.tick()
        self.assertEqual(self.color(), "red")
        self.devices.sensors["ultrasonic"]["distance"] = 5.0
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")
        self.devices.sensors["ultrasonic"]["distance"] = 7.9
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")
        self.devices.sensors["ultrasonic"]["distance"] = 8.0
        self.bridge.tick()
        self.assertEqual(self.color(), "green")
        self.dispatch("5A A5 06 83 20 60 01 40 60")
        self.assertEqual(self.color(), "off")

    def test_stale_and_out_of_range_distance_clear_green(self):
        self.bridge.handle_event("ultrasonic.start")
        self.wall[0] = 106
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")
        state = self.devices.sensors["ultrasonic"]
        state.update(sample_time=106, distance=401)
        count = len(self.screen.frames)
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")
        self.assertEqual(len(self.screen.frames), count)

    def test_red_priority_and_stop_one_preserves_other_scene(self):
        self.bridge.handle_event("body_temperature.start")
        self.devices.sensors["ultrasonic"]["distance"] = 3
        self.bridge.handle_event("ultrasonic.start")
        self.assertEqual(self.color(), "red")
        self.bridge.handle_event("body_temperature.stop")
        self.assertEqual(self.color(), "red")
        self.bridge.handle_event("ultrasonic.stop")
        self.assertEqual(self.color(), "off")

    def test_unchanged_color_does_not_rewrite_gpio(self):
        self.bridge.handle_event("ultrasonic.start")
        calls = len(self.devices.calls)
        self.bridge.tick()
        self.bridge.tick()
        self.assertEqual(len(self.devices.calls), calls)

    def test_disabled_rgb_still_updates_screen(self):
        cfg = replace(DEFAULT_CONFIG, rgb=replace(DEFAULT_CONFIG.rgb, enabled=False))
        bridge = ScreenDeviceBridge(self.screen, self.devices, config=cfg, wall_clock=lambda: 100)
        bridge.handle_event("body_temperature.start")
        self.assertEqual(self.devices.calls, [])
        self.assertTrue(self.screen.frames)

    def test_waiting_off_policy_releases_rgb_after_stopping(self):
        cfg = replace(DEFAULT_CONFIG, rgb_feedback=replace(DEFAULT_CONFIG.rgb_feedback, waiting_color="off"))
        self.devices.sensors["body_temperature"]["temperature"] = None
        bridge = ScreenDeviceBridge(self.screen, self.devices, config=cfg, wall_clock=lambda: 100)
        bridge.handle_event("body_temperature.start")
        bridge.handle_event("body_temperature.stop")
        before = len(self.devices.calls)
        bridge.feedback.flush({"actuators": {"rgb": {"color": "green"}}})
        self.assertEqual(len(self.devices.calls), before)

    def test_application_passes_custom_feedback_thresholds(self):
        from application import NingBoApplication
        cfg = replace(DEFAULT_CONFIG, rgb_feedback=replace(DEFAULT_CONFIG.rgb_feedback,
                                                         distance_danger_cm=100, distance_caution_cm=120))
        app = NingBoApplication(config=cfg, devices=self.devices, screen=self.screen)
        app.bridge.feedback.wall_clock = lambda: 100
        app.bridge.handle_event("ultrasonic.start")
        self.assertEqual(self.color(), "red")

    def test_failed_gpio_write_retries_without_skipping_display(self):
        with patch.object(self.devices, "control", side_effect=OSError("GPIO failed")):
            with self.assertLogs("Serial_Screen.device_bridge", level="ERROR"):
                self.bridge.handle_event("ultrasonic.start")
        self.assertTrue(self.screen.frames)
        self.bridge.tick()
        self.assertEqual(self.color(), "green")

    def test_access_result_queue_session_and_command_ack(self):
        self.bridge.access.handler = lambda name: {"ok": True, "success": True}
        response = self.bridge.handle_event("access.start")
        token = response["session_id"]
        self.assertEqual(self.color(), "blue")
        self.assertTrue(self.bridge.report_access_result(False, token))
        self.assertEqual(self.color(), "blue")  # 回调线程只入队，不写 GPIO。
        self.bridge.tick()
        self.assertEqual(self.color(), "red")
        self.assertTrue(self.bridge.report_access_result(True, token))
        self.bridge.tick()
        self.assertEqual(self.color(), "green")
        self.bridge.handle_event("access.stop")
        self.assertEqual(self.color(), "off")
        self.assertFalse(self.bridge.report_access_result(True, token))
        new_token = self.bridge.handle_event("access.start")["session_id"]
        self.assertNotEqual(token, new_token)
        self.assertFalse(self.bridge.report_access_result(False, token))
        self.assertEqual(self.color(), "blue")
        self.assertTrue(all(name == "rgb" for name, _ in self.devices.calls))

    def test_access_synchronous_result_and_pending_does_not_override_red(self):
        self.bridge.access.handler = lambda name: {"recognized": False}
        self.bridge.handle_event("access.start")
        self.assertEqual(self.color(), "red")
        self.bridge.handle_event("ultrasonic.start")
        self.assertEqual(self.color(), "red")
        self.bridge.handle_event("access.stop")
        self.assertEqual(self.color(), "green")

    def test_stopped_access_discards_queued_result(self):
        token = self.bridge.handle_event("access.start")["session_id"]
        self.bridge.report_access_result(False, token)
        self.bridge.handle_event("access.stop")
        self.bridge.handle_event("access.start")
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")

    def test_invalid_access_result_and_handler_failure(self):
        token = self.bridge.handle_event("access.start")["session_id"]
        with self.assertRaises(ValueError):
            self.bridge.report_access_result("success", token)
        self.bridge.access.handler = lambda name: (_ for _ in ()).throw(OSError("camera failed"))
        with self.assertRaises(OSError):
            self.bridge.handle_event("access.start")
        self.assertFalse(self.bridge.access_enabled)
        self.assertEqual(self.color(), "off")

    def test_parameter_frames_wait_for_start_and_single_motor_stop(self):
        self.dispatch("5A A5 06 83 10 61 01 00 5A")  # 舵机 90 度
        self.assertEqual(self.devices.calls, [])
        self.dispatch("5A A5 06 83 30 40 01 50 40")
        self.assertEqual(self.devices.calls[-1], ("servo", {"sweep": True}))
        self.dispatch("5A A5 06 83 30 50 01 50 50")
        self.assertEqual(self.devices.calls[-1], ("servo", {"stop": True}))
        self.dispatch("5A A5 08 83 10 62 02 00 01 00 64")
        self.dispatch("5A A5 06 83 30 60 01 50 60")
        self.assertEqual(self.devices.calls[-1], ("stepper", {"dir_": "rev", "n": 100, "spd": 8}))
        self.dispatch("5A A5 06 83 30 70 01 50 70")
        self.assertEqual(self.devices.calls[-1], ("stepper", {"stop": True}))

    def test_stepper_start_without_editable_controls_uses_defaults_and_can_stop(self):
        self.assertEqual(self.devices.calls, [])
        self.assertTrue(self.dispatch("5A A5 06 83 30 60 01 50 60")[0]["handled"])
        self.assertEqual(self.devices.calls, [("stepper", {"dir_": "fwd", "n": 512, "spd": 8})])
        self.dispatch("5A A5 06 83 30 70 01 50 70")
        self.assertEqual(self.devices.calls[-1], ("stepper", {"stop": True}))

    def test_invalid_stepper_parameter_discards_old_direction_and_uses_default(self):
        self.dispatch("5A A5 06 83 10 62 01 00 01")
        self.dispatch("5A A5 06 83 10 63 01 00 64")
        with self.assertRaises(ValueError):
            self.dispatch("5A A5 06 83 10 62 01 00 FF")
        self.assertEqual(self.devices.calls, [])
        self.assertTrue(self.bridge.handle_event("stepper.start")["handled"])
        self.assertEqual(self.devices.calls, [("stepper", {"dir_": "fwd", "n": 100, "spd": 8})])
        calls_before = list(self.devices.calls)
        self.assertEqual(ScreenEventParser().feed(bytes.fromhex("5A A5 06 83 10 61 02 00 5A")), [])
        self.assertEqual(self.devices.calls, calls_before)

    def test_voice_hook_and_bridge_shutdown(self):
        events = []
        self.assertFalse(self.bridge.handle_event("voice.start")["handled"])
        self.bridge.voice.handler = lambda name: events.append(name)
        self.bridge.access.handler = lambda name: events.append(name)
        self.bridge.handle_event("voice.start")
        token = self.bridge.handle_event("access.start")["session_id"]
        self.bridge.stop()
        self.assertIn("voice.stop", events)
        self.assertIn("access.stop", events)
        self.assertFalse(self.bridge.report_access_result(True, token))
        self.assertEqual(self.bridge.active_scenes, {})


class DriverBridgeTests(unittest.TestCase):
    def test_business_result_reaches_real_rgb_driver_and_configured_pins(self):
        cfg = fan_only_config()
        cfg = replace(cfg, fan=replace(cfg.fan, enabled=False), rgb=replace(cfg.rgb, enabled=True, active_high=False),
                      body_temperature=replace(cfg.body_temperature, enabled=True, model="injected-real-driver"))
        class Reader:
            def read(self): return {"temperature": 36.8}
        raw_gpio = FakeGPIO()
        device = DeviceService(config=cfg, gpio=GpioResource(cfg, module=raw_gpio), body_temperature_driver=Reader())
        with device:
            device.manager.poll_sensor("body_temperature")
            bridge = ScreenDeviceBridge(MemoryScreen(), device, config=cfg)
            bridge.handle_event("body_temperature.start")
            self.assertEqual([raw_gpio.levels[p] for p in cfg.rgb.pins], [1, 0, 1])
            device.manager._readers["body_temperature"].read = lambda: {"temperature": 38.0}
            device.manager.poll_sensor("body_temperature")
            bridge.tick()
            self.assertEqual([raw_gpio.levels[p] for p in cfg.rgb.pins], [0, 1, 1])
            self.assertEqual(device.check("body_temperature")["status"], "normal")
            bridge.handle_event("body_temperature.stop")
            self.assertEqual([raw_gpio.levels[p] for p in cfg.rgb.pins], [1, 1, 1])

    def test_body_driver_required_before_hardware_opening(self):
        cfg = replace(DEFAULT_CONFIG, body_temperature=replace(DEFAULT_CONFIG.body_temperature, enabled=True, model="module"))
        with self.assertRaisesRegex(ValueError, "尚未注入"):
            DeviceManager(config=cfg)

    def test_single_output_stop_preserves_other_outputs(self):
        cfg = fan_only_config()
        cfg = replace(cfg, rgb=replace(cfg.rgb, enabled=True))
        raw_gpio = FakeGPIO()
        with DeviceService(config=cfg, gpio=GpioResource(cfg, module=raw_gpio)) as device:
            device.control("fan", on=True)
            device.control("rgb", color="green")
            device.stop_actuator("fan")
            self.assertFalse(device.snapshot()["actuators"]["fan"]["on"])
            self.assertEqual(device.snapshot()["actuators"]["rgb"]["color"], "green")

    def test_diagnostic_bridge_filters_removed_screen_devices(self):
        class Devices:
            def diagnostic_snapshot(self):
                return {"revision": 1, "results": {"dht11": {"status": "normal"}, "current": {"status": "normal"},
                        "oled": {"status": "normal"}}, "busy": False, "progress": 100, "overall": "normal"}
        screen = MemoryScreen()
        records = DiagnosticsScreenBridge(screen, Devices()).tick()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["address"], 0x6000)

    def test_invalid_feedback_thresholds_are_rejected(self):
        for kwargs in ({"body_temperature_high_c": float("nan")}, {"distance_danger_cm": -1},
                       {"distance_danger_cm": 8, "distance_caution_cm": 5},
                       {"distance_caution_cm": 401},
                       {"sample_max_age_seconds": 0}, {"waiting_color": "green"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, rgb_feedback=replace(DEFAULT_CONFIG.rgb_feedback, **kwargs)).validate()


if __name__ == "__main__":
    unittest.main()
