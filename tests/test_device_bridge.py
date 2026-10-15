"""Integration tests: original HMI frames -> real bridge -> device API -> HMI frames.

No Jetson, GPIO, serial port, I2C bus or PCB needed.
"""
import unittest

from Serial_Screen.device_bridge import ScreenDeviceBridge
from Serial_Screen.screen_service.screen.screen_events import ScreenEventParser
from Serial_Screen.screen_service.screen.screen_sender import ScreenSender


class MemoryScreen:
    def __init__(self):
        from Serial_Screen.screen_service.screen.message_parser import MessageParser
        self.parser = MessageParser()
        self.messages = []
        self.frames = []

    def handle_message(self, message):
        self.messages.append(message)
        records = []
        for item in self.parser.parse(message):
            frame = (ScreenSender.build_write_bytes_frame(item["address"], item["data"]) if "data" in item else
                     ScreenSender.build_write_frame(item["address"], item["value"]))
            records.append({**item, "frame": frame})
            self.frames.append(frame)
        return records


class FakeDevice:
    def __init__(self, sensors=None):
        self.sensors = sensors or {}
        self.calls = []

    def snapshot(self):
        return {"sensors": self.sensors, "actuators": {}}

    def control(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return {"on": kwargs.get("on")}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.screen = MemoryScreen()
        self.devices = FakeDevice({
            "dht11": {"temp": 25.36, "humidity": 63.20},
            "bh1750": {"lux": 428.0},
            "ultrasonic": {"distance": 125.2},
            "smoke": {"online": True, "raw": "a1b2c3", "percent": None},
        })
        self.bridge = ScreenDeviceBridge(self.screen, self.devices, clock=lambda: self.now[0])

    def test_real_original_button_bytes_to_hmi_frame(self):
        event_parser = ScreenEventParser()
        # Original event: environment.start VP=0x2000 value 0x4000.
        events = event_parser.feed(bytes.fromhex("5A A5 06 83 20 00 01 40 00"))
        self.assertEqual(events[0]["event"], "environment.start")
        response = self.bridge.handle_event(events[0])
        self.assertTrue(response["handled"])
        self.assertEqual(self.screen.frames, [
            bytes.fromhex("5A A5 05 82 10 00 09 E8"),  # 25.36
            bytes.fromhex("5A A5 05 82 10 10 18 B0"),  # 63.20
            bytes.fromhex("5A A5 05 82 10 20 00 2B"),  # lux->43 percent
        ])
        self.now[0] = 0.4
        self.bridge.tick()
        self.assertEqual(len(self.screen.frames), 3)
        self.now[0] = 1.0
        self.bridge.tick()
        self.assertEqual(len(self.screen.frames), 6)

    def test_stop_clears_and_stops_refresh(self):
        self.bridge.handle_event("environment.start")
        self.bridge.handle_event("environment.stop")
        self.assertEqual(self.screen.messages[-1],
                         {"scene": "environment", "temperature": 0, "humidity": 0, "light": 0})
        count = len(self.screen.frames)
        self.now[0] = 5.0
        self.bridge.tick()
        self.assertEqual(len(self.screen.frames), count)

    def test_fan_events_reach_device_service(self):
        self.bridge.handle_event("fan.start")
        self.bridge.handle_event("fan.stop")
        self.assertEqual(self.devices.calls,
                         [("fan", {"on": True}), ("fan", {"on": False})])

    def test_access_buttons_do_not_directly_unlock(self):
        self.assertFalse(self.bridge.handle_event("access.start")["handled"])
        self.assertTrue(self.bridge.access_enabled)
        self.assertFalse(self.bridge.handle_event("access.face_enroll")["handled"])
        self.assertFalse(self.bridge.handle_event("access.stop")["handled"])
        self.assertFalse(self.bridge.access_enabled)
        self.assertEqual(self.devices.calls, [("rgb", {"color": "blue"}), ("rgb", {"color": "off"})])

    def test_smoke_raw_uart_is_not_falsified_as_percentage(self):
        self.bridge.handle_event("smoke.start")
        self.assertEqual(self.screen.frames, [])
        self.devices.sensors["smoke"]["percent"] = 72
        self.now[0] = 1.0
        self.bridge.tick()
        self.assertEqual(self.screen.frames[-1], bytes.fromhex("5A A5 05 82 10 50 00 48"))

    def test_unavailable_body_temperature_does_not_fabricate_data(self):
        response = self.bridge.handle_event("body_temperature.start")
        self.assertIn("尚未接入", response["note"])
        self.assertFalse(self.screen.frames)

    def test_ultrasonic_can_refresh_from_snapshot(self):
        self.bridge.handle_event("ultrasonic.start")
        # 距离控件保留一位小数，125.2 cm 编码为原始 Word 1252。
        self.assertEqual(self.screen.frames[0], bytes.fromhex("5A A5 05 82 10 40 04 E4"))

    def test_light_normalization_is_bounded(self):
        self.devices.sensors["bh1750"]["lux"] = 9999
        self.bridge.handle_event("environment.start")
        self.assertEqual(self.screen.frames[-1], bytes.fromhex("5A A5 05 82 10 20 00 64"))

    def test_new_control_and_status_buttons_are_in_hmi_event_map(self):
        from Serial_Screen.screen_service.screen.addresses import BUTTON_EVENT_MAP
        self.assertEqual(len(BUTTON_EVENT_MAP), 22)
        self.assertEqual(BUTTON_EVENT_MAP[(0x3040, 0x5040)], "servo.start")
        self.assertEqual(BUTTON_EVENT_MAP[(0x3070, 0x5070)], "stepper.stop")
        self.assertEqual(BUTTON_EVENT_MAP[(0x3080, 0x5080)], "voice.start")
        self.assertEqual(BUTTON_EVENT_MAP[(0x9000, 0x8000)], "diagnostics.all")


if __name__ == "__main__":
    unittest.main()
