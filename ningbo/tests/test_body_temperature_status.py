"""GY-906 protocol response and original adjacent status VP regression checks."""
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch
from config import DEFAULT_CONFIG
from experiments.body_temperature.jetson.diagnostics import check_body_temperature
from experiments.body_temperature.jetson.driver import Mlx90614Driver, smbus_pec
from screen_service import MessageParser
from hardware.screen.sender import ScreenSender
from hardware.screen.frames import ScreenEventParser
from system.diagnostics.jetson.service import DiagnosticService


class BodyTemperatureStatusTests(unittest.TestCase):
    def manager(self, states=None):
        return SimpleNamespace(config=DEFAULT_CONFIG, _readers={"body_temperature": object()},
            _started=True, _stop=threading.Event(), _pintest_mode=False,
            poll_sensor=Mock(side_effect=states))

    def good(self, temperature=23.4):
        return {"status": "normal", "temperature": temperature,
                "ambient_temperature": 24., "sample_time": time.time() + 1}

    def check(self, manager):
        with patch("experiments.body_temperature.jetson.diagnostics.RETRY_SECONDS", 0):
            return check_body_temperature(manager)

    def test_room_temperature_is_normal_without_a_person(self):
        manager = self.manager([self.good()])
        result = self.check(manager)
        self.assertEqual(result["status"], "normal")
        self.assertEqual(result["attempts"], 1)
        manager.poll_sensor.assert_called_once_with("body_temperature")

    def test_transient_error_retries_and_accepts_new_data(self):
        manager = self.manager([{"status": "error", "note": "I2C error"}, self.good()])
        self.assertEqual(self.check(manager)["status"], "normal")
        self.assertEqual(manager.poll_sensor.call_count, 2)

    def test_no_response_has_bounded_retries_and_is_error(self):
        manager = self.manager([{ "status": "error", "note": "No ACK"}] * 3)
        result = self.check(manager)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["attempts"], 3)
        self.assertIn("No ACK", result["note"])

    def test_old_nan_bool_and_missing_data_cannot_pass(self):
        states = [dict(self.good(), sample_time=0), self.good(float("nan")),
                  self.good(True), {"status": "normal"},
                  dict(self.good(), ambient_temperature=200)]
        for state in states:
            with self.subTest(state=state):
                self.assertEqual(self.check(self.manager([state] * 3))["status"], "error")

    def test_disabled_maintenance_and_shutdown_cannot_reuse_previous_data(self):
        for mode in ("disabled", "maintenance", "shutdown"):
            manager = self.manager([self.good()])
            if mode == "disabled": manager._readers = {}
            if mode == "maintenance": manager._pintest_mode = True
            if mode == "shutdown": manager._stop.set()
            self.assertEqual(self.check(manager)["status"], "error")
            manager.poll_sensor.assert_not_called()

    def test_actual_driver_checks_pec_and_error_bit(self):
        cfg = DEFAULT_CONFIG.body_temperature
        def response(address, register, count):
            raw = round((25 + 273.15) / .02)
            low, high = raw & 255, raw >> 8
            return [low, high, smbus_pec([address << 1, register, (address << 1) | 1, low, high])]
        bus = SimpleNamespace(lock=threading.RLock(), read_register_bytes=Mock(side_effect=response))
        driver = Mlx90614Driver(bus, cfg)
        result = driver.read()
        self.assertAlmostEqual(result["temperature"], 25., delta=.02)
        bus.read_register_bytes.side_effect = lambda a, r, n: response(a, r, n)[:2] + [response(a,r,n)[2] ^ 1]
        with self.assertRaisesRegex(ValueError, "PEC"): driver.read()
        def error_flag(a, r, n):
            low, high = 0, 128
            return [low, high, smbus_pec([a << 1, r, (a << 1) | 1, low, high])]
        bus.read_register_bytes.side_effect = error_flag
        with self.assertRaisesRegex(ValueError, "错误标志"): driver.read()

    def test_sensor_button_schedules_body_temperature(self):
        event = ScreenEventParser().feed(bytes.fromhex("5aa506839001018001"))[0]
        self.assertEqual(event["event"], "diagnostics.sensors")
        self.assertIn("body_temperature", DiagnosticService.targets("sensors"))

    def test_three_status_frames_only_touch_6003(self):
        memory = {0x6002: b"SM", 0x6004: b"US"}
        for status, code in (("checking", "检"), ("normal", "好"), ("error", "坏")):
            update = MessageParser().parse({"scene": "diagnostics",
                "statuses": {"body_temperature": status}})[0]
            self.assertEqual(update["address"], 0x6003)
            self.assertEqual(update["data"], code.encode("gb2312"))
            frame = ScreenSender.build_write_bytes_frame(update["address"], update["data"])
            self.assertEqual(frame[:6], bytes.fromhex("5aa505826003"))
            self.assertEqual(len(frame), 8)
            memory[0x6003] = frame[6:]
        self.assertEqual(memory[0x6002], b"SM")
        self.assertEqual(memory[0x6004], b"US")


if __name__ == "__main__": unittest.main()
