"""设备重构验证：配置下传、故障、极性、协议和线程资源生命周期。"""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from JETSON.Sensor import DEFAULT_CONFIG, DeviceService
from JETSON.Sensor.sensors.current import CurrentMonitor
from JETSON.Sensor.sensors.light import LightDriver
from JETSON.Sensor.sensors.smoke import SmokeDriver
from JETSON.Sensor.actuators.stepper import StepperDriver
from JETSON.Sensor.manager import DeviceManager
from JETSON.Sensor.resources import GpioResource, I2cResource


class FakeGPIO:
    BOARD = "BOARD"
    OUT = "OUT"
    IN = "IN"
    HIGH = 1
    LOW = 0
    PUD_UP = "UP"

    def __init__(self):
        self.levels = {}
        self.writes = []
        self.cleaned = []
        self.fail_writes = False

    def setmode(self, mode): self.mode = mode
    def setwarnings(self, enabled): pass
    def setup(self, pin, direction, initial=None, **kwargs):
        if initial is not None:
            self.levels[pin] = initial
    def output(self, pin, level):
        if self.fail_writes:
            raise OSError("GPIO write failed")
        self.levels[pin] = level
        self.writes.append((pin, level))
    def input(self, pin): return self.levels.get(pin, 0)
    def cleanup(self, pins): self.cleaned.extend(pins)


def fan_only_config():
    cfg = DEFAULT_CONFIG
    values = {name: replace(getattr(cfg, name), enabled=False) for name in
              ("dht11", "light", "ultrasonic", "smoke", "body_temperature", "oled", "servo", "stepper", "rgb", "buzzer", "lock")}
    values["fan"] = replace(cfg.fan, pin=40, active_high=False)
    return replace(cfg, **values)


class ArchitectureTests(unittest.TestCase):
    def test_pin_conflict_rejected_before_hardware_loading(self):
        cfg = replace(DEFAULT_CONFIG, fan=replace(DEFAULT_CONFIG.fan, pin=DEFAULT_CONFIG.servo.pin))
        with self.assertRaisesRegex(ValueError, "冲突"):
            DeviceService(config=cfg)

    def test_real_driver_uses_configured_pin_and_active_low(self):
        cfg = fan_only_config()
        raw_gpio = FakeGPIO()
        gpio = GpioResource(cfg, module=raw_gpio)
        with DeviceService(config=cfg, gpio=gpio) as device:
            self.assertEqual(raw_gpio.levels[40], 1)  # 启动时保持关闭
            self.assertEqual(device.control("fan", on=True), {"on": True})
            self.assertEqual(raw_gpio.levels[40], 0)
            self.assertTrue(device.snapshot()["actuators"]["fan"]["on"])
            device.emergency_stop()
            self.assertEqual(raw_gpio.levels[40], 1)
        self.assertIn(40, raw_gpio.cleaned)

    def test_write_failure_does_not_record_success(self):
        cfg = fan_only_config()
        raw_gpio = FakeGPIO()
        gpio = GpioResource(cfg, module=raw_gpio)
        with DeviceService(config=cfg, gpio=gpio) as device:
            raw_gpio.fail_writes = True
            try:
                with self.assertRaisesRegex(OSError, "write failed"):
                    device.control("fan", on=True)
                self.assertFalse(device.snapshot()["actuators"]["fan"]["on"])
            finally:
                raw_gpio.fail_writes = False

    def test_failed_read_clears_old_sensor_value(self):
        class Sensor:
            def read(self): raise OSError("sensor disconnected")
        manager = DeviceManager(config=fan_only_config(), sensor_drivers={"bh1750": Sensor()}, output_drivers={})
        manager.sensors["bh1750"]["lux"] = 100
        state = manager.poll_sensor("bh1750")
        self.assertIsNone(state["lux"])
        self.assertEqual(state["status"], "error")
        self.assertIn("disconnected", state["note"])

    def test_smoke_port_baud_and_decoder_are_injected(self):
        class Serial:
            in_waiting = 2
            def __init__(self, **kwargs): self.kwargs = kwargs; self.closed = False
            def read(self, size): return b"\x00\x2A"
            def close(self): self.closed = True
        cfg = replace(DEFAULT_CONFIG.smoke, port="TEST_PORT", baudrate=19200, protocol="raw")
        driver = SmokeDriver(cfg, serial_factory=Serial)
        driver.start()
        connection = driver.serial
        self.assertEqual(connection.kwargs["port"], "TEST_PORT")
        self.assertEqual(connection.kwargs["baudrate"], 19200)
        self.assertIsNone(driver.read()["percent"])
        driver.decoder = lambda raw: 42
        self.assertEqual(driver.read()["percent"], 42)
        driver.close()
        self.assertTrue(connection.closed)

    def test_ads_conversion_range_matches_register(self):
        class Bus:
            def write_i2c_block_data(self, address, register, values): self.config_word = values
            def read_i2c_block_data(self, address, register, count):
                return self.config_word if register == 1 else [0x40, 0x00]
        bus = I2cResource(8, factory=lambda number: Bus())
        monitor = CurrentMonitor(bus, replace(DEFAULT_CONFIG.current, enabled=True))
        self.assertEqual(monitor.read_ma("lock"), 1024.0)
        self.assertEqual(bus.bus.config_word, [0xC5, 0x83])

    def test_light_uses_one_time_mode_and_raw_i2c_read(self):
        class Bus:
            def __init__(self): self.commands = []
            def write_byte(self, address, value): self.commands.append((address, value))
        class Resource:
            lock = threading.RLock()
            bus = Bus()
            def open(self): pass
            def read_bytes(self, address, count): return [0x01, 0x20]
        resource = Resource()
        with patch("JETSON.Sensor.sensors.light.time.sleep"):
            result = LightDriver(resource, replace(DEFAULT_CONFIG.light, address=0x5C)).read()
        self.assertEqual(result, {"lux": 240.0})
        self.assertEqual(resource.bus.commands, [(0x5C, 0x01), (0x5C, 0x20)])

    def test_stepper_reverse_reverses_phase_index_and_stop_deenergizes(self):
        cfg = DEFAULT_CONFIG
        gpio = GpioResource(cfg, module=FakeGPIO())
        driver = StepperDriver(gpio, cfg.stepper)
        driver.set(dir_="rev", n=3)
        driver.step_once()
        driver.step_once()
        # 第一次第0相，第二次最后一相；引脚顺序始终相同。
        values = [level for _, level in gpio.module.writes]
        self.assertEqual(values[:8], list(cfg.stepper.sequence[0] + cfg.stepper.sequence[-1]))
        driver.stop()
        self.assertEqual([gpio.module.levels[pin] for pin in cfg.stepper.pins], [0, 0, 0, 0])

    def test_shutdown_joins_workers_and_can_restart(self):
        read_completed = threading.Event()
        class Sensor:
            def read(self):
                read_completed.set()
                return {"temp": 25, "humidity": 50}
        cfg = fan_only_config()
        gpio = GpioResource(cfg, module=FakeGPIO())
        device = DeviceService(config=cfg, gpio=gpio, sensor_drivers={"dht11": Sensor()})
        device.start()
        self.assertTrue(read_completed.wait(1))
        workers = list(device.manager._threads)
        device.control("fan", on=True)
        device.pintest_on()
        with self.assertRaisesRegex(RuntimeError, "引脚测试"):
            device.control("fan", on=True)
        device.stop()
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertFalse(device.snapshot()["actuators"]["fan"]["on"])
        device.start()
        try:
            self.assertEqual(device.control("fan", on=True), {"on": True})
        finally:
            device.stop()

    def test_public_actuator_api_checks_feedback_without_changing_outputs(self):
        cfg = fan_only_config()
        cfg = replace(cfg, current=replace(cfg.current, enabled=True),
                      actuator_diagnostics=replace(cfg.actuator_diagnostics, sample_interval=0))
        raw_gpio = FakeGPIO()
        gpio = GpioResource(cfg, module=raw_gpio)
        with DeviceService(config=cfg, gpio=gpio) as device:
            class Monitor:
                def read_ma(self, name): return 100 if raw_gpio.levels[cfg.fan.pin] == 0 else 0
            device.manager.current_monitor = Monitor()
            self.assertEqual(device.check_actuator("fan")["status"], "idle")
            device.control("fan", on=True)
            writes_before = list(raw_gpio.writes)
            report = device.check_actuators()
            self.assertEqual(len(report["results"]), 7)
            self.assertEqual(report["results"]["fan"]["status"], "normal")
            self.assertEqual(raw_gpio.writes, writes_before)
            accepted = device.request_diagnostics(group="actuators")
            self.assertTrue(accepted["accepted"])
            device.diagnostics._worker.join(1)
            self.assertFalse(device.diagnostic_snapshot()["busy"])
            self.assertEqual(device.diagnostic_snapshot()["results"]["fan"]["status"], "normal")

    def test_actuator_diagnostic_configuration_rejects_invalid_limits(self):
        channels = dict(DEFAULT_CONFIG.current.channels)
        channels["fan"] = replace(channels["fan"], max_current_ma=channels["fan"].threshold_ma)
        cfg = replace(DEFAULT_CONFIG, current=replace(DEFAULT_CONFIG.current, channels=channels))
        with self.assertRaisesRegex(ValueError, "运行电流上限"):
            cfg.validate()
        cfg = replace(DEFAULT_CONFIG, actuator_diagnostics=replace(DEFAULT_CONFIG.actuator_diagnostics, sample_count=0))
        with self.assertRaisesRegex(ValueError, "采样次数"):
            cfg.validate()


if __name__ == "__main__":
    unittest.main()
