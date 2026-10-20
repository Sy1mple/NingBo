"""对业务程序提供统一设备 API，业务层无需导入 GPIO 或总线驱动。"""
import threading
from config import Config, DEFAULT_CONFIG

from device_manager import ACTUATORS, DeviceManager
from system.diagnostics.jetson import DiagnosticService
from system.diagnostics.jetson.definitions import ACTUATOR_TARGETS

class DeviceService:
    def __init__(self, config=None, manager=None, **driver_options):
        self.manager = manager or DeviceManager(config=config, **driver_options)
        self.mode = self.manager.mode
        self.config = self.manager.config
        self.diagnostics = DiagnosticService(self.manager)
        self._started = False
        self._lifecycle_lock = threading.RLock()

    def start(self):
        with self._lifecycle_lock:
            if not self._started:
                self.manager.start()
                self._started = True

    def stop(self):
        with self._lifecycle_lock:
            if self._started:
                self.diagnostics.stop()
                self.manager.stop()
                self._started = False

    def snapshot(self):
        return self.manager.snapshot()

    def read_adc(self):
        """读取真实四路电压；不把未标定电压解释为设备运行状态。"""
        with self._lifecycle_lock:
            if not self._started:
                raise RuntimeError("请先启动 DeviceService")
            return self.manager.adc_monitor.read_all()

    def check(self, name):
        if not self._started:
            raise RuntimeError("请先启动 DeviceService")
        return self.diagnostics.check(name)

    def check_all(self, group="all"):
        if not self._started:
            raise RuntimeError("请先启动 DeviceService")
        return self.diagnostics.check_all(group)

    def check_actuator(self, name):
        if name not in ACTUATOR_TARGETS:
            raise ValueError(f"未知执行器: {name}")
        return self.check(name)

    def check_actuators(self):
        return self.check_all(group="actuators")

    def request_diagnostics(self, group="all", target=None):
        return self.diagnostics.request(group, target)

    def diagnostic_snapshot(self):
        return self.diagnostics.snapshot()

    def acquire_sensor(self, name, client):
        if not self._started:
            raise RuntimeError("请先启动 DeviceService")
        return self.manager.acquire_sensor(name, client)

    def release_sensor(self, name, client):
        return self.manager.release_sensor(name, client)

    def reconnect_sensor(self, name, client):
        if not self._started:
            raise RuntimeError("请先启动 DeviceService")
        return self.manager.reconnect_sensor(name, client)

    def control(self, name, **kwargs):
        if name not in ACTUATORS:
            raise ValueError(f"未知执行器: {name}")
        if not self._started:
            raise RuntimeError("请先启动 DeviceService")
        return self.manager.control(name, **kwargs)

    def emergency_stop(self):
        return self.manager.emergency_stop()

    def stop_actuator(self, name):
        if name not in ACTUATORS:
            raise ValueError(f"未知执行器: {name}")
        if not self._started:
            raise RuntimeError("请先启动 DeviceService")
        return self.manager.stop_actuator(name)

    def pintest_on(self): return self.manager.pintest_on()
    def pintest_off(self): return self.manager.pintest_off()
    def pintest_set(self, pin, level): return self.manager.pintest_set(pin, level)
    def pintest_status(self): return self.manager.pintest_status()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stop()
        return False
