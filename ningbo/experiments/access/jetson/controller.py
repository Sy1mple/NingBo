"""access: owns driver construction, sampling/control, and its shutdown steps."""
from copy import deepcopy
from .lock_gpio import LockDriver

READER_EMPTY_VALUES = {}
READER_CONFIG_NAMES = {}
ACTUATORS = ('lock',)


class Hardware:
    def __init__(self, owner, **options):
        self.owner = owner
        cfg = owner.config
        self._readers, self._outputs = {}, {}
        self._threads, self._ready_outputs = [], set()
        if cfg.lock.enabled:
            self._outputs["lock"] = LockDriver(owner.gpio, cfg.lock)
        supplied = options.get("sensor_drivers")
        if supplied is not None:
            self._readers = {name: driver for name, driver in supplied.items() if name in READER_EMPTY_VALUES}
        supplied = options.get("output_drivers")
        if supplied is not None:
            self._outputs = {name: driver for name, driver in supplied.items() if name in ACTUATORS}
        for name in ACTUATORS:
            owner.actuators[name] = {"on": False, "status": None, "current_ma": None}
        self._update_outputs()

    def initialize_outputs(self):
        for name, driver in self._outputs.items():
            driver.start()
            self._ready_outputs.add(name)

    def stop_outputs(self, close=False):
        errors = {}
        for name in tuple(self._ready_outputs):
            driver = self._outputs[name]
            try:
                action = driver.close if close and hasattr(driver, "close") else driver.stop
                action()
            except Exception as exc:
                errors[name] = str(exc)
        return errors

    def _update_outputs(self):
        with self.owner._lock:
            for name, driver in self._outputs.items():
                state = driver.snapshot()
                if state.get("status") is None:
                    state.pop("status", None)
                    if not state.get("note"):
                        state.pop("note", None)
                self.owner.actuators[name].update(state)
            for name in ACTUATORS:
                if name not in self._outputs:
                    self.owner.actuators[name].update(status="disabled", note="config.py 中未启用")

    def control(self, name, **kwargs):
        with self.owner._control_lock:
            if not self.owner._started:
                raise RuntimeError("设备服务尚未启动")
            if self.owner._pintest_mode:
                raise RuntimeError("引脚测试期间禁止正常控制")
            if name not in self._outputs:
                raise ValueError(f"执行器未启用或不存在: {name}")
            try:
                result = self._outputs[name].set(**kwargs)
            except Exception as exc:
                with self.owner._lock:
                    self.owner.actuators[name].update(status="error", note=str(exc), control_error=str(exc))
                raise
            self._update_outputs()
            with self.owner._lock:
                self.owner.actuators[name].update(status=None, note="", current_ma=None, control_error="")
            return result

    def stop_actuator(self, name):
        """只停止指定执行器，使用驱动自己的关闭电平和资源锁。"""
        with self.owner._control_lock:
            if not self.owner._started:
                raise RuntimeError("设备服务尚未启动")
            if self.owner._pintest_mode:
                raise RuntimeError("引脚测试期间禁止正常控制")
            if name not in self._outputs:
                raise ValueError(f"执行器未启用或不存在: {name}")
            try:
                self._outputs[name].stop()
            except Exception as exc:
                with self.owner._lock:
                    self.owner.actuators[name].update(status="error", note=str(exc), control_error=str(exc))
                raise
            self._update_outputs()
            with self.owner._lock:
                self.owner.actuators[name].update(status=None, note="", current_ma=None, control_error="")
                return deepcopy(self.owner.actuators[name])

    def record_actuator_diagnostic(self, name, result):
        with self.owner._lock:
            self.owner.actuators[name].update(
                current_ma=result.get("current_ma"), status=result["status"],
                note=result.get("note", ""),
            )

    def actuator_output_levels(self, name):
        """读取静态输出的实际 GPIO 电平；PWM/步进不使用此比较。"""
        with self.owner._control_lock:
            config = getattr(self.owner.config, name)
            pins = (config.pin,)
            return {pin: self.owner.gpio.read(pin) for pin in pins}
