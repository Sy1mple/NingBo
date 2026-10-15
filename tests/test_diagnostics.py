from copy import deepcopy
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from JETSON.Sensor import DEFAULT_CONFIG
from JETSON.Sensor.diagnostics import DiagnosticService
from JETSON.Sensor.actuators.outputs import RgbDriver
from Serial_Screen.diagnostics_bridge import DiagnosticsScreenBridge
from Serial_Screen.screen_service.screen import addresses
from Serial_Screen.screen_service.screen.message_parser import MessageParser
from Serial_Screen.screen_service.screen.screen_events import ScreenEventParser
from Serial_Screen.screen_service.screen.screen_sender import ScreenSender


class ManagerFixture:
    def __init__(self):
        self._started = True
        self.config = DEFAULT_CONFIG
        self._control_lock = threading.RLock()
        self._outputs = {name: object() for name in ("fan", "servo", "stepper", "buzzer", "rgb", "lock")}
        self.actuators = {name: {"on": False, "status": None} for name in self._outputs}
        self.sensors = {
            "dht11": {"temp": 25.0, "humidity": 50.0, "status": "normal"},
            "bh1750": {"lux": 100.0, "status": "normal"},
            "ultrasonic": {"distance": 50.0, "status": "normal"},
            "smoke": {"online": True, "raw": "AA55", "percent": None, "status": "normal"},
            "oled": {"ok": True, "status": "normal"},
        }
        self.ma = 0.0
        self.forced_levels = {}
        class Monitor:
            def read_ma(inner, name): return self.ma
        self.current_monitor = Monitor()

    def poll_sensor(self, name): return deepcopy(self.sensors[name])
    def snapshot(self): return {"actuators": deepcopy(self.actuators)}
    def actuator_state(self, name): return deepcopy(self.actuators[name])
    def actuator_output_levels(self, name):
        config = getattr(self.config, name)
        if name == "rgb":
            pattern = RgbDriver.COLORS[self.actuators[name].get("color", "off")]
            values = {pin: int(bool(value) == config.active_high) for pin, value in zip(config.pins, pattern)}
        else:
            values = {config.pin: int(bool(self.actuators[name].get("on")) == config.active_high)}
        values.update(self.forced_levels.get(name, {}))
        return values
    def record_actuator_diagnostic(self, name, result):
        self.last_diagnostic = result
        self.actuators[name].update(status=result["status"], current_ma=result.get("current_ma"), note=result["note"])


class DiagnosticTests(unittest.TestCase):
    def test_sensor_success_missing_value_and_failure_are_distinct(self):
        manager = ManagerFixture()
        diagnostics = DiagnosticService(manager)
        self.assertEqual(diagnostics.check("dht11")["status"], "normal")
        manager.sensors["dht11"]["temp"] = None
        self.assertEqual(diagnostics.check("dht11")["status"], "error")
        manager.sensors["bh1750"] = {"status": "error", "note": "I2C failed"}
        self.assertEqual(diagnostics.check("light")["note"], "I2C failed")

    def test_serial_bytes_do_not_prove_smoke_sensor_health(self):
        manager = ManagerFixture()
        diagnostics = DiagnosticService(manager)
        self.assertEqual(diagnostics.check("smoke")["status"], "unconfigured")
        manager.sensors["smoke"].update(decoder_configured=True)
        self.assertEqual(diagnostics.check("smoke")["status"], "unknown")
        manager.sensors["smoke"]["percent"] = 40
        self.assertEqual(diagnostics.check("smoke")["status"], "normal")

    def test_actuator_feedback_needs_config_and_running_evidence(self):
        manager = ManagerFixture()
        diagnostics = DiagnosticService(manager)
        self.assertEqual(diagnostics.check("fan")["status"], "unconfigured")
        manager.config = replace(DEFAULT_CONFIG, current=replace(DEFAULT_CONFIG.current, enabled=True))
        self.assertEqual(diagnostics.check("fan")["status"], "idle")
        manager.actuators["fan"]["on"] = True
        self.assertEqual(diagnostics.check("fan")["status"], "error")
        manager.ma = 100
        self.assertEqual(diagnostics.check("fan")["status"], "normal")
        manager.actuators["fan"]["on"] = False
        self.assertEqual(diagnostics.check("fan")["status"], "error")

    def test_full_check_includes_unconfigured_devices_and_oled_output(self):
        result = DiagnosticService(ManagerFixture()).check_all()
        self.assertEqual(len(result["results"]), 13)
        self.assertEqual(result["overall"], "unconfigured")
        self.assertEqual(result["results"]["body_temperature"]["status"], "unconfigured")
        self.assertEqual(result["results"]["oled"]["category"], "actuator")

    def test_background_check_does_not_block_and_rejects_duplicate(self):
        manager = ManagerFixture()
        entered, release = threading.Event(), threading.Event()
        def reader(name):
            entered.set()
            release.wait(1)
            return manager.sensors[name]
        manager.poll_sensor = reader
        diagnostics = DiagnosticService(manager)
        try:
            self.assertTrue(diagnostics.request(target="dht11")["accepted"])
            self.assertTrue(entered.wait(1))
            self.assertTrue(diagnostics.snapshot()["busy"])
            self.assertFalse(diagnostics.request()["accepted"])
        finally:
            release.set()
            diagnostics._worker.join(1)
            diagnostics.stop()
        self.assertFalse(diagnostics.snapshot()["busy"])
        self.assertEqual(diagnostics.snapshot()["results"]["dht11"]["status"], "normal")


class ActuatorStatusTests(unittest.TestCase):
    def setUp(self):
        self.manager = ManagerFixture()
        self.manager.config = replace(
            DEFAULT_CONFIG,
            current=replace(DEFAULT_CONFIG.current, enabled=True),
            actuator_diagnostics=replace(DEFAULT_CONFIG.actuator_diagnostics, sample_interval=0),
        )
        self.diagnostics = DiagnosticService(self.manager)

    def test_previous_feedback_error_can_recover_without_new_command(self):
        self.manager.actuators["fan"]["on"] = True
        self.assertEqual(self.diagnostics.check("fan")["status"], "error")
        self.assertEqual(self.manager.actuators["fan"]["status"], "error")
        self.manager.ma = 100
        result = self.diagnostics.check("fan")
        self.assertEqual(result["status"], "normal")
        self.assertEqual(self.manager.actuators["fan"]["status"], "normal")

    def test_gpio_mismatch_is_error_even_with_running_current(self):
        self.manager.actuators["fan"]["on"] = True
        self.manager.ma = 100
        self.manager.forced_levels["fan"] = {self.manager.config.fan.pin: 0}
        result = self.diagnostics.check("fan")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["signal"]["status"], "error")

    def test_current_median_filters_one_low_sample(self):
        self.manager.actuators["fan"]["on"] = True
        samples = iter([0.0, 100.0, 100.0])
        self.manager.current_monitor.read_ma = lambda name: next(samples)
        result = self.diagnostics.check("fan")
        self.assertEqual(result["status"], "normal")
        self.assertEqual(result["current_ma"], 100)
        self.assertEqual(result["feedback"]["samples_ma"], [0, 100, 100])

    def test_current_peak_over_calibrated_limit_is_error(self):
        channels = dict(self.manager.config.current.channels)
        channels["fan"] = replace(channels["fan"], max_current_ma=150)
        self.manager.config = replace(self.manager.config, current=replace(self.manager.config.current, channels=channels))
        self.manager.actuators["fan"]["on"] = True
        samples = iter([100.0, 200.0, 100.0])
        self.manager.current_monitor.read_ma = lambda name: next(samples)
        result = self.diagnostics.check("fan")
        self.assertEqual(result["status"], "error")
        self.assertIn("运行上限", result["note"])

    def test_off_current_has_its_own_calibrated_limit(self):
        channels = dict(self.manager.config.current.channels)
        channels["lock"] = replace(channels["lock"], off_current_ma=5)
        self.manager.config = replace(self.manager.config, current=replace(self.manager.config.current, channels=channels))
        self.manager.ma = 10
        self.assertEqual(self.diagnostics.check("lock")["status"], "error")

    def test_running_motors_require_live_control_threads(self):
        for name, flag in (("servo", "on"), ("stepper", "running")):
            with self.subTest(name=name):
                self.manager.actuators[name].update({flag: True, "worker_alive": False})
                result = self.diagnostics.check(name)
                self.assertEqual(result["status"], "error")
                self.assertIn("控制线程未运行", result["note"])
                self.manager.actuators[name]["worker_alive"] = True
                self.manager.ma = 100
                self.assertEqual(self.diagnostics.check(name)["status"], "normal")

    def test_driver_failure_is_not_hidden_by_good_current(self):
        self.manager.actuators["buzzer"].update(on=True, control_error="GPIO write failed")
        self.manager.ma = 100
        result = self.diagnostics.check("buzzer")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["driver"]["note"], "GPIO write failed")

    def test_rgb_checks_each_channel_with_active_low_config(self):
        self.manager.config = replace(self.manager.config, rgb=replace(self.manager.config.rgb, active_high=False))
        self.manager.actuators["rgb"].update(on=True, color="blue")
        self.manager.ma = 100
        result = self.diagnostics.check("rgb")
        self.assertEqual(result["status"], "normal")
        self.assertEqual(list(result["signal"]["expected"].values()), [1, 1, 0])
        self.manager.forced_levels["rgb"] = {self.manager.config.rgb.pins[2]: 1}
        self.assertEqual(self.diagnostics.check("rgb")["status"], "error")

    def test_oled_failure_is_communication_result(self):
        self.manager.sensors["oled"] = {"ok": False, "status": "error", "note": "I2C disconnected"}
        result = self.diagnostics.check("oled")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["check_scope"], "communication")

    def test_missing_channel_is_not_reported_normal(self):
        channels = dict(self.manager.config.current.channels)
        channels.pop("fan")
        self.manager.config = replace(self.manager.config, current=replace(self.manager.config.current, channels=channels))
        self.manager.actuators["fan"]["on"] = True
        self.assertEqual(self.diagnostics.check("fan")["status"], "unconfigured")


class StatusAddressTests(unittest.TestCase):
    def test_status_page_uses_only_visible_devices_and_group_buttons(self):
        self.assertNotIn("current", addresses.STATUS_RESULT_ADDRESS_MAP)
        self.assertNotIn("oled", addresses.STATUS_RESULT_ADDRESS_MAP)
        self.assertEqual(len(addresses.STATUS_RESULT_ADDRESS_MAP), 11)
        self.assertEqual(set(addresses.configured_status_button_events().values()), {
            "diagnostics.all", "diagnostics.sensors", "diagnostics.actuators",
        })
        result = MessageParser().parse({"scene": "diagnostics", "statuses": {"dht11": "normal"},
                                        "busy": False, "progress": 100, "overall": "normal"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["address"], 0x6000)
        self.assertEqual(result[0]["value"], addresses.STATUS_CODE_MAP["normal"])

    def test_configured_button_frame_and_status_write(self):
        # 此地址只属于测试输入，不写入项目的预留地址。
        with patch.dict(addresses.STATUS_CHECK_BINDINGS, {"diagnostics.all": (0x6000, 0x1234)}):
            events = addresses.configured_status_button_events()
        with patch.dict(addresses.BUTTON_EVENT_MAP, events):
            received = ScreenEventParser().feed(bytes.fromhex("5A A5 06 83 60 00 01 12 34"))
        self.assertEqual(received[0]["event"], "diagnostics.all")
        with patch.dict(addresses.STATUS_RESULT_ADDRESS_MAP, {"dht11": 0x6100}):
            updates = MessageParser().parse({"scene": "diagnostics", "statuses": {"dht11": "normal"}})
        self.assertEqual(ScreenSender.build_write_frame(updates[0]["address"], updates[0]["value"]),
                         bytes.fromhex("5A A5 05 82 61 00 00 01"))

    def test_partial_button_configuration_and_existing_conflict_are_rejected(self):
        with patch.dict(addresses.STATUS_CHECK_BINDINGS, {"diagnostics.all": (0x6000, None)}):
            with self.assertRaises(ValueError): addresses.configured_status_button_events()
        with patch.dict(addresses.STATUS_CHECK_BINDINGS, {"diagnostics.all": (0x2000, 0x4000)}):
            with self.assertRaisesRegex(ValueError, "冲突"): addresses.configured_status_button_events()

    def test_result_address_cannot_overwrite_measurement_or_another_result(self):
        with patch.dict(addresses.STATUS_RESULT_ADDRESS_MAP, {"dht11": 0x1000}):
            with self.assertRaisesRegex(ValueError, "冲突"):
                addresses.validate_status_result_addresses()
        with patch.dict(addresses.STATUS_RESULT_ADDRESS_MAP, {"dht11": 0x6100, "fan": 0x6100}):
            with self.assertRaisesRegex(ValueError, "冲突"):
                addresses.validate_status_result_addresses()

    def test_bridge_sends_report_revision_once_and_no_run_commands(self):
        class Devices:
            calls = []
            def request_diagnostics(self, **kwargs):
                self.calls.append(kwargs)
                return {"accepted": True, "note": "已开始状态检测"}
            def diagnostic_snapshot(self):
                return {"revision": 1, "results": {"fan": {"status": "idle"}},
                        "busy": False, "progress": 100, "overall": "idle"}
        class Screen:
            messages = []
            def handle_message(self, message): self.messages.append(message); return [message]
        devices, screen = Devices(), Screen()
        bridge = DiagnosticsScreenBridge(screen, devices)
        bridge.handle_event("diagnostics.actuators")
        self.assertEqual(devices.calls, [{"group": "actuators"}])
        self.assertEqual(len(bridge.tick()), 1)
        self.assertEqual(bridge.tick(), [])
        self.assertEqual(screen.messages[0]["statuses"], {"fan": "idle"})


if __name__ == "__main__":
    unittest.main()
