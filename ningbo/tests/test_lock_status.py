"""Door-lock electrical response through the real driver and ADS1115/A1 mapping."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from experiments.access.jetson.lock_gpio import LockDriver
from experiments.access.jetson.diagnostics import LockStatusCheck
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser


class Gpio:
    def __init__(self):
        self.levels, self.calls = {}, []
    def open(self): pass
    def close(self): pass
    def setup_output(self, pin, level=0):
        self.levels[pin] = level; self.calls.append(("setup", pin, level))
    def write(self, pin, level):
        self.levels[pin] = level; self.calls.append(("write", pin, level))
    def read(self, pin): return self.levels[pin]


class LockStatusTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG, lock=replace(DEFAULT_CONFIG.lock,
            diagnostic_on_settle_seconds=.001, diagnostic_off_settle_seconds=.001))
        self.gpio = Gpio()
        self.driver = LockDriver(self.gpio, self.config.lock)
        self.manager = DeviceManager(config=self.config, gpio=self.gpio,
            sensor_drivers={}, output_drivers={"lock": self.driver})
        self.manager.start(); self.addCleanup(self.manager.stop)
        self.volts = {False: -.000625, True: .274875}
        self.manager.adc_monitor = SimpleNamespace(read_channel=Mock(side_effect=lambda *a, **kw: {
            "volts": self.volts[self.driver.on], "saturated": False}))
        patcher = patch("experiments.access.jetson.diagnostics.time.sleep")
        patcher.start(); self.addCleanup(patcher.stop)

    def check(self, event=None):
        return LockStatusCheck(self.manager).check("lock", event)

    def test_manual_check_uses_a1_and_restores_locked_state(self):
        result = self.check()
        self.assertEqual(result["status"], "normal")
        self.assertEqual(result["before"]["levels"], {29: 0})
        self.assertEqual(result["energized"]["levels"], {29: 1})
        self.assertEqual(result["after"]["levels"], {29: 0})
        self.assertEqual(result["restored_levels"], {29: 0})
        self.assertFalse(self.driver.on)
        self.manager.adc_monitor.read_channel.assert_called_with(
            1, address=0x48, full_scale_volts=.512)
        self.assertEqual(self.manager.adc_monitor.read_channel.call_count, 45)

    def test_missing_or_constant_feedback_fails(self):
        for off, on in ((-.000625, -.000625), (.274875, .274875)):
            with self.subTest(off=off, on=on):
                self.volts.update({False: off, True: on})
                self.assertEqual(self.check()["status"], "error")
                self.assertEqual(self.gpio.levels, {29: 0})

    def test_existing_unlocked_state_is_restored(self):
        self.manager.control("lock", on=True)
        result = self.check()
        self.assertEqual(result["status"], "normal")
        self.assertTrue(result["restored_on"])
        self.assertTrue(self.driver.on)
        self.assertEqual(self.gpio.levels, {29: 1})

    def test_cancel_while_energized_restores_locked_state(self):
        event = threading.Event()
        original = self.manager.adc_monitor.read_channel.side_effect
        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.driver.on: event.set()
            return result
        self.manager.adc_monitor.read_channel.side_effect = read
        self.assertEqual(self.check(event)["status"], "unknown")
        self.assertFalse(self.driver.on)
        self.assertEqual(self.gpio.levels, {29: 0})

    def test_fixed_6010_status_uses_font_100_phrase_codes(self):
        for status, text in (("checking", "检"), ("normal", "好"), ("error", "坏")):
            item = MessageParser().parse(
                {"scene": "diagnostics", "statuses": {"lock": status}})[0]
            self.assertEqual(item["address"], 0x6010)
            self.assertEqual(item["text"], text)
            data = text.encode("gb2312")
            self.assertEqual(ScreenSender.build_write_bytes_frame(item["address"], item["data"]),
                             bytes.fromhex("5aa505826010") + data)


if __name__ == "__main__": unittest.main()
