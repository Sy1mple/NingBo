"""MLX90614 协议、故障清除及屏幕/RGB 集成验证，不打开真实硬件。"""
from dataclasses import replace
import threading
import unittest

from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Sensor.manager import DeviceManager
from JETSON.Sensor.resources import I2cResource
from JETSON.Sensor.sensors.mlx90614 import Mlx90614Driver, smbus_pec
from JETSON.Sensor.diagnostics.sensor_checks import SensorStatusChecker
from Serial_Screen.device_bridge import ScreenDeviceBridge
from Serial_Screen.screen_service.screen.message_parser import MessageParser
from test_sensor_architecture import fan_only_config


class RegisterResource:
    def __init__(self, address=0x5A):
        self.lock = threading.RLock()
        self.address = address
        self.raw = {0x06: 14900, 0x07: 15500}  # 24.85°C / 36.85°C
        self.requests = []
        self.corrupt = False
        self.short = False
        self.failure = False

    def read_register_bytes(self, address, register, count):
        self.requests.append((address, register, count))
        if self.failure:
            raise OSError("disconnected")
        value = self.raw[register]
        low, high = value & 0xFF, value >> 8
        pec = smbus_pec((address << 1, register, (address << 1) | 1, low, high))
        response = [low, high, pec ^ int(self.corrupt)]
        return response[:2] if self.short else response


class Mlx90614Tests(unittest.TestCase):
    def setUp(self):
        self.resource = RegisterResource()
        self.config = DEFAULT_CONFIG.body_temperature
        self.driver = Mlx90614Driver(self.resource, self.config)

    def test_manufacturer_pec_example_and_little_endian_conversion(self):
        self.assertEqual(smbus_pec((0xB4, 0x07, 0xB5, 0xD2, 0x3A)), 0x30)
        result = self.driver.read()
        self.assertEqual(result["temperature"], 36.85)
        self.assertEqual(result["ambient_temperature"], 24.85)
        self.assertEqual(self.resource.requests, [(0x5A, 0x06, 3), (0x5A, 0x07, 3)])

    def test_pec_failure_is_rejected_instead_of_displayed(self):
        self.resource.corrupt = True
        with self.assertRaisesRegex(ValueError, "PEC"):
            self.driver.read()

    def test_error_flag_and_out_of_range_are_rejected(self):
        for register, value in ((0x07, 0x8000), (0x06, 0x8000), (0x07, 0), (0x06, 0)):
            with self.subTest(register=register, value=value):
                self.resource.raw = {0x06: 14900, 0x07: 15500, register: value}
                with self.assertRaises(ValueError):
                    self.driver.read()

    def test_short_response_and_disconnect_are_not_accepted(self):
        self.resource.short = True
        with self.assertRaisesRegex(OSError, "长度"):
            self.driver.read()
        self.resource.short = False
        self.resource.failure = True
        with self.assertRaisesRegex(OSError, "disconnected"):
            self.driver.read()

    def test_nondefault_address_is_used_in_transaction_and_pec(self):
        driver = Mlx90614Driver(self.resource, replace(self.config, address=0x5B))
        self.assertEqual(driver.read()["temperature"], 36.85)
        self.assertEqual(self.resource.requests[0][0], 0x5B)

    def test_config_rejects_invalid_address_and_ranges(self):
        for values in ({"address": True}, {"address": 0x80}, {"ambient_range": (1, 0)},
                       {"temperature_range": (float("nan"), 380)}, {"display_range": (-1, 100)}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, body_temperature=replace(self.config, **values)).validate()

    def test_register_transport_uses_combined_transaction(self):
        class Message:
            def __init__(self, address, data): self.address, self.data = address, list(data)
            def __iter__(self): return iter(self.data)
        class Messages:
            @staticmethod
            def write(address, data): return Message(address, data)
            @staticmethod
            def read(address, count): return Message(address, [0] * count)
        class Bus:
            def i2c_rdwr(inner, command, response):
                self.assertEqual((command.address, command.data), (0x5A, [0x07]))
                self.assertEqual(len(response.data), 3)
                response.data[:] = [0xD2, 0x3A, 0x30]
        resource = I2cResource(8, factory=lambda number: Bus(), message_factory=Messages)
        self.assertEqual(resource.read_register_bytes(0x5A, 0x07, 3), [0xD2, 0x3A, 0x30])

    def test_builtin_driver_polling_diagnostics_and_fault_recovery(self):
        cfg = fan_only_config()
        cfg = replace(cfg, fan=replace(cfg.fan, enabled=False), body_temperature=self.config)
        manager = DeviceManager(config=cfg, i2c=self.resource, output_drivers={})
        self.assertIsInstance(manager._readers["body_temperature"], Mlx90614Driver)
        self.assertEqual(manager.poll_sensor("body_temperature")["temperature"], 36.85)
        self.assertEqual(SensorStatusChecker(manager).check("body_temperature")["status"], "normal")
        self.resource.corrupt = True
        failed = manager.poll_sensor("body_temperature")
        self.assertEqual(failed["status"], "error")
        self.assertIsNone(failed["temperature"])
        self.assertIsNone(failed["ambient_temperature"])
        self.resource.corrupt = False
        self.assertEqual(manager.poll_sensor("body_temperature")["status"], "normal")

    def test_room_temperature_displays_without_claiming_normal_body_temperature(self):
        cfg = fan_only_config()
        cfg = replace(cfg, fan=replace(cfg.fan, enabled=False), body_temperature=self.config)
        manager = DeviceManager(config=cfg, i2c=self.resource, output_drivers={})
        self.resource.raw[0x07] = 14900
        manager.poll_sensor("body_temperature")
        bridge = ScreenDeviceBridge(None, manager, config=cfg)
        bridge.feedback.activate("body_temperature")
        snapshot = manager.snapshot()
        bridge.feedback.observe(snapshot["sensors"])
        self.assertEqual(bridge.feedback.color(), "blue")
        values = bridge._scene_values("body_temperature", snapshot)
        updates = MessageParser().parse({"scene": "body_temperature", **values})
        self.assertEqual((updates[0]["address"], updates[0]["value"]), (0x1030, 2485))
        for raw, color in ((15500, "green"), (15575, "red")):
            self.resource.raw[0x07] = raw
            manager.poll_sensor("body_temperature")
            bridge.feedback.observe(manager.snapshot()["sensors"])
            self.assertEqual(bridge.feedback.color(), color)


if __name__ == "__main__":
    unittest.main()
