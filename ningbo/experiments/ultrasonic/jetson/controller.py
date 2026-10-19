"""ultrasonic: owns driver construction, sampling/control, and its shutdown steps."""
from copy import deepcopy
import threading
import time
from .gpio import UltrasonicDriver
from .buzzer_gpio import BuzzerDriver

READER_EMPTY_VALUES = {'ultrasonic': {'distance': None}}
READER_CONFIG_NAMES = {'ultrasonic': 'ultrasonic'}
ACTUATORS = ('buzzer',)


class Hardware:
    def __init__(self, owner, **options):
        self.owner = owner
        cfg = owner.config
        self._readers, self._outputs = {}, {}
        self._threads, self._ready_outputs = [], set()
        if cfg.ultrasonic.enabled:
            self._readers["ultrasonic"] = UltrasonicDriver(owner.gpio, cfg.ultrasonic)
        if cfg.buzzer.enabled:
            self._outputs["buzzer"] = BuzzerDriver(owner.gpio, cfg.buzzer)
        supplied = options.get("sensor_drivers")
        if supplied is not None:
            self._readers = {name: driver for name, driver in supplied.items() if name in READER_EMPTY_VALUES}
        supplied = options.get("output_drivers")
        if supplied is not None:
            self._outputs = {name: driver for name, driver in supplied.items() if name in ACTUATORS}
        for name, values in READER_EMPTY_VALUES.items():
            states = owner.sensors
            states[name] = {**values, "status": None, "note": "", "sample_time": None}
            if name not in self._readers:
                states[name].update(status="disabled", note="config.py 中未启用")
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

    def initialize_readers(self):
        for name, driver in self._readers.items():
            try:
                if hasattr(driver, "start"):
                    driver.start()
            except Exception as exc:
                self.owner._init_errors.append(f"{name}: {exc}")

    def start_sampling(self):
        for name in self._readers:
            worker = threading.Thread(target=self._sensor_loop, args=(name,), name="sensor-" + name, daemon=True)
            self._threads.append(worker)
            worker.start()

    def close_readers(self):
        errors = {}
        for name, driver in self._readers.items():
            try:
                if hasattr(driver, "close"):
                    driver.close()
            except Exception as exc:
                errors[name] = str(exc)
        return errors

    def _sensor_loop(self, name):
        interval = getattr(self.owner.config, READER_CONFIG_NAMES[name]).interval
        while not self.owner._stop.is_set():
            if not self.owner._pintest_mode:
                self.poll_sensor(name)
            if self.owner._stop.wait(interval):
                break

    def poll_sensor(self, name, response_only=False):
        states = self.owner.sensors
        if name not in self._readers:
            return deepcopy(states[name])
        with self.owner._sampling_condition:
            if self.owner._pintest_mode or self.owner._stop.is_set():
                if response_only:
                    return {"status": "error", "note": "设备维护或停止期间无法检测响应"}
                return deepcopy(states[name])
            self.owner._active_reads += 1
        try:
            try:
                reader = self._readers[name]
                state = reader.read_response() if response_only else reader.read()
                online = state.get("online", True) and state.get("ok", True)
                status = "normal" if online else "error"
                note = "" if online else "本次采样无有效数据"
                state.update(status=status, note=note)
                state.setdefault("sample_time", time.time())
            except Exception as exc:
                state = {**READER_EMPTY_VALUES[name], "status": "error", "note": str(exc), "sample_time": time.time()}
        finally:
            with self.owner._sampling_condition:
                self.owner._active_reads -= 1
                self.owner._sampling_condition.notify_all()
        with self.owner._lock:
            # Diagnostic responses may be outside the ranging limits. Keep them
            # out of the distance/alarm cache used by the running scene.
            if not response_only:
                states[name] = state
            return deepcopy(state)

