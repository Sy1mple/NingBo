"""Lifecycle and dispatch ONLY; driver construction and operations are scene-owned."""
from copy import deepcopy
from importlib import import_module
import threading
import time
import logging
from config import DEFAULT_CONFIG
from hardware.gpio import GpioResource
from hardware.i2c import I2cResource
from hardware.pinmap import board_pinmap
from hardware.adc.current import CurrentMonitor
from hardware.adc.voltage import Ads1115Monitor
from experiments import HARDWARE_MODULES
from system.pin_test import PinTest

MODULES = tuple(import_module(name) for name in HARDWARE_MODULES)
ACTUATORS = ("servo", "lock", "fan", "rgb", "stepper", "buzzer")
READER_EMPTY_VALUES = {name: fields for module in MODULES for name, fields in module.READER_EMPTY_VALUES.items()}
READER_CONFIG_NAMES = {name: cfg for module in MODULES for name, cfg in module.READER_CONFIG_NAMES.items()}
LOGGER = logging.getLogger(__name__)


class DeviceManager:
    def __init__(self, config=None, gpio=None, i2c=None, smoke_decoder=None, sensor_drivers=None,
                 output_drivers=None, body_temperature_driver=None):
        self.config = (config or DEFAULT_CONFIG).validate()
        self.mode, self.version = "hw", "3.1.0"
        self._lock, self._control_lock = threading.RLock(), threading.RLock()
        self._sampling_condition = threading.Condition()
        self._active_reads = 0
        self._stop = threading.Event()
        self._started, self._pintest_mode = False, False
        self._init_errors, self._pintest_levels = [], {}
        self._output_start_errors = {}
        self.gpio, self.i2c = gpio or GpioResource(self.config), i2c or I2cResource(self.config.i2c_bus)
        self.pinmap = board_pinmap(self.config)
        self.sensors, self.actuators = {}, {}
        options = dict(smoke_decoder=smoke_decoder, sensor_drivers=sensor_drivers,
                       output_drivers=output_drivers, body_temperature_driver=body_temperature_driver)
        self.scenes = {module.__name__: module.Hardware(self, **options) for module in MODULES}
        self._sensor_owners = {name: self.scenes[module.__name__] for module in MODULES for name in module.READER_EMPTY_VALUES}
        self._output_owners = {name: self.scenes[module.__name__] for module in MODULES for name in module.ACTUATORS}
        self.current_monitor = CurrentMonitor(self.i2c, self.config.current)
        self.adc_monitor = Ads1115Monitor(self.i2c, self.config.ads1115)
        self.pintest_in = {self.config.ultrasonic.echo_pin: "超声ECHO"} if self.config.ultrasonic.enabled else {}
        self.pintest_out = {pin: name for pin, name in self.config.gpio_devices().items() if pin not in self.pintest_in}
        self.pin_test = PinTest(self)

    @property
    def _readers(self):
        return {name: driver for scene in self.scenes.values() for name, driver in scene._readers.items()}

    @property
    def _outputs(self):
        return {name: driver for scene in self.scenes.values() for name, driver in scene._outputs.items()}

    @property
    def _threads(self):
        return [worker for scene in self.scenes.values() for worker in scene._threads]

    @property
    def _ready_outputs(self):
        return {name for scene in self.scenes.values() for name in scene._ready_outputs}

    def _phase(self, name):
        for scene in self.scenes.values():
            action = getattr(scene, name, None)
            if action is not None:
                action()

    def start(self):
        with self._control_lock:
            if self._started:
                return
            self._init_errors.clear()
            self._output_start_errors.clear()
            self._stop.clear()
            try:
                if self.config.gpio_devices():
                    self.gpio.open()
                # 接管静态控制脚并写入各驱动配置的关闭电平，避免保留上电输入/上拉状态。
                # 门锁/风扇/RGB 当前为低电平关闭，低有效蜂鸣器为高电平关闭。
                # 舵机 PWM 和步进相序仍等待按钮，初始化不执行动作或状态检测。
                for name in ("lock", "fan", "rgb", "buzzer"):
                    scene = self._output_owners[name]
                    if name not in scene._outputs:
                        continue
                    # start 中途失败时，也要清理已经分配的输出。
                    scene._ready_outputs.add(name)
                    scene._outputs[name].start()
                # 步进四相先保持低电平；相序线程仍由第一次显式按钮启动。
                self._phase("initialize_idle_outputs")
                self._phase("initialize_readers")
                self._started = True
                self._phase("start_sampling")
            except Exception:
                try:
                    self.stop()
                except Exception as exc:
                    self._init_errors.append(f"rollback: {exc}")
                raise

    def stop(self):
        with self._control_lock:
            self._stop.set()
            self._pintest_mode = self._started = False
            errors = {}
            for scene in self.scenes.values():
                if hasattr(scene, "stop_outputs"):
                    errors.update(scene.stop_outputs(close=True))
            for worker in self._threads:
                worker.join(self.config.thread_join_timeout)
            if any(worker.is_alive() for worker in self._threads):
                raise RuntimeError("采集线程未结束，保留硬件资源以避免线程访问已关闭连接")
            with self._sampling_condition:
                if not self._sampling_condition.wait_for(lambda: self._active_reads == 0, self.config.thread_join_timeout):
                    raise RuntimeError("诊断采样未结束，硬件资源保持打开")
            for scene in self.scenes.values():
                scene._threads.clear()
                scene._ready_outputs.clear()
                if hasattr(scene, "close_readers"):
                    errors.update(scene.close_readers())
            for name, resource in (("i2c", self.i2c), ("gpio", self.gpio)):
                try:
                    resource.close()
                except Exception as exc:
                    errors[name] = str(exc)
            self._update_outputs()
            if errors:
                raise RuntimeError("Device close failed: " + repr(errors))

    def _update_outputs(self):
        self._phase("_update_outputs")
        with self._lock:
            for name, state in self.actuators.items():
                state['initialized'] = name in self._ready_outputs and name not in self._output_start_errors

    def prepare_actuator(self, name):
        """仅供显式控制/引脚维护调用；快照和状态检测不得隐式初始化输出。"""
        with self._control_lock:
            if not self._started:
                raise RuntimeError("设备服务尚未启动")
            if self._pintest_mode:
                raise RuntimeError("引脚测试期间禁止初始化执行器")
            if name not in self._output_owners:
                raise ValueError(f"Unknown actuator: {name}")
            scene = self._output_owners[name]
            if name not in scene._outputs:
                raise ValueError(f"执行器未启用或不存在: {name}")
            if name in self._output_start_errors:
                raise RuntimeError(f"执行器 {name} 初始化曾失败，请停止并重启设备服务")
            if name in scene._ready_outputs:
                return
            # 先纳入清理集合，确保 start 部分失败后仍会关闭已分配的引脚/线程。
            scene._ready_outputs.add(name)
            try:
                scene._outputs[name].start()
            except Exception as exc:
                self._output_start_errors[name] = str(exc)
                try:
                    driver = scene._outputs[name]
                    action = driver.close if hasattr(driver, 'close') else driver.stop
                    action()
                except Exception:
                    LOGGER.exception("执行器 %s 初始化失败后的清理异常", name)
                with self._lock:
                    self.actuators[name].update(initialized=False, status='error', control_error=str(exc), note=str(exc))
                raise
            LOGGER.info("显式控制首次初始化执行器：%s", name)
            self._update_outputs()

    def snapshot(self):
        self._update_outputs()
        with self._lock:
            return {"meta": {"mode": self.mode, "version": self.version, "time": time.time(),
                             "init_errors": list(self._init_errors), "pins": deepcopy(self.pinmap)},
                    "sensors": deepcopy(self.sensors), "actuators": deepcopy(self.actuators)}

    def poll_sensor(self, name, response_only=False):
        if response_only:
            if name != "ultrasonic":
                raise ValueError("响应检测仅用于超声波")
            return self._sensor_owners[name].poll_sensor(name, response_only=True)
        return self._sensor_owners[name].poll_sensor(name)

    def acquire_sensor(self, name, client):
        if not self._started:
            raise RuntimeError("设备服务尚未启动")
        owner = self._sensor_owners.get(name)
        action = getattr(owner, "acquire_reader", None)
        if action is None:
            return self.poll_sensor(name)
        return action(name, client)

    def release_sensor(self, name, client):
        owner = self._sensor_owners.get(name)
        action = getattr(owner, "release_reader", None)
        if action is None:
            return None
        return action(name, client)

    def reconnect_sensor(self, name, client):
        if not self._started:
            raise RuntimeError("设备服务尚未启动")
        owner = self._sensor_owners.get(name)
        action = getattr(owner, "reconnect_reader", None)
        if action is None:
            raise ValueError(f"传感器不支持重连: {name}")
        return action(name, client)

    def control(self, name, **kwargs):
        if name not in self._output_owners:
            raise ValueError(f"Unknown actuator: {name}")
        with self._control_lock:
            self.prepare_actuator(name)
            return self._output_owners[name].control(name, **kwargs)

    def stop_actuator(self, name):
        if name not in self._output_owners:
            raise ValueError(f"Unknown actuator: {name}")
        with self._control_lock:
            self.prepare_actuator(name)
            return self._output_owners[name].stop_actuator(name)

    def record_actuator_diagnostic(self, name, result):
        return self._output_owners[name].record_actuator_diagnostic(name, result)

    def actuator_output_levels(self, name):
        return self._output_owners[name].actuator_output_levels(name)

    def actuator_state(self, name):
        with self._control_lock:
            return self.snapshot()["actuators"][name]

    def emergency_stop(self, resume_sampling=True):
        with self._control_lock:
            errors = {}
            for scene in self.scenes.values():
                if hasattr(scene, "stop_outputs"):
                    errors.update(scene.stop_outputs())
            errors.update(self.pin_test.reset_other_outputs())
            self._update_outputs()
            self._pintest_mode = not resume_sampling
            return {"ok": not errors, "errors": errors}

    def pintest_on(self): return self.pin_test.pintest_on()
    def pintest_off(self): return self.pin_test.pintest_off()
    def pintest_set(self, pin, level): return self.pin_test.pintest_set(pin, level)
    def pintest_status(self): return self.pin_test.pintest_status()
