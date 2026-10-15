"""设备编排：输入驱动在 sensors，输出驱动在 actuators，状态检测独立处理。"""
from copy import deepcopy
import threading
import time

from .Config import DEFAULT_CONFIG
from .pinmap import board_pinmap
from .resources import GpioResource, I2cResource


READER_EMPTY_VALUES = {
    "dht11": {"temp": None, "humidity": None}, "ultrasonic": {"distance": None},
    "bh1750": {"lux": None}, "smoke": {"online": None, "raw": None, "percent": None, "concentration": None},
    "body_temperature": {"temperature": None, "ambient_temperature": None},
    "oled": {"ok": None},
}
READER_CONFIG_NAMES = {"dht11": "dht11", "ultrasonic": "ultrasonic", "bh1750": "light", "smoke": "smoke", "oled": "oled",
                       "body_temperature": "body_temperature"}
ACTUATORS = ("servo", "lock", "fan", "rgb", "stepper", "buzzer")


class DeviceManager:
    def __init__(self, config=None, gpio=None, i2c=None, smoke_decoder=None, sensor_drivers=None, output_drivers=None,
                 body_temperature_driver=None):
        self.config = (config or DEFAULT_CONFIG).validate()
        if (self.config.body_temperature.enabled and self.config.body_temperature.model != "mlx90614"
                and body_temperature_driver is None and "body_temperature" not in (sensor_drivers or {})):
            raise ValueError("人体温度已启用，但尚未注入真实 body_temperature_driver")
        if (body_temperature_driver is not None or "body_temperature" in (sensor_drivers or {})) and not self.config.body_temperature.enabled:
            raise ValueError("注入人体温度驱动前请在 Config.py 启用并填写型号")
        self.mode = "hw"
        self.version = "3.1.0"
        self._lock = threading.RLock()
        self._control_lock = threading.RLock()
        self._sampling_condition = threading.Condition()
        self._active_reads = 0
        self._stop = threading.Event()
        self._threads = []
        self._ready_outputs = set()
        self._started = False
        self._init_errors = []
        self._pintest_mode = False
        self._pintest_levels = {}
        self.gpio = gpio or GpioResource(self.config)
        self.i2c = i2c or I2cResource(self.config.i2c_bus)
        self.pinmap = board_pinmap(self.config)
        self.sensors = {name: {**values, "status": None, "note": "", "sample_time": None} for name, values in READER_EMPTY_VALUES.items() if name != "oled"}
        self.actuators = {name: {"on": False, "status": None, "current_ma": None} for name in ACTUATORS}
        self.actuators["oled"] = {"ok": None, "status": None, "note": "", "sample_time": None}
        self._readers, self._outputs = self._build_drivers(smoke_decoder)
        if sensor_drivers is not None:
            self._readers = sensor_drivers
        if body_temperature_driver is not None:
            self._readers = {**self._readers, "body_temperature": body_temperature_driver}
        if output_drivers is not None:
            self._outputs = output_drivers
        used = self.config.gpio_devices()
        self.pintest_in = {self.config.ultrasonic.echo_pin: "超声ECHO"} if self.config.ultrasonic.enabled else {}
        self.pintest_out = {pin: name for pin, name in used.items() if pin not in self.pintest_in}
        for name in READER_EMPTY_VALUES:
            if name not in self._readers:
                states = self.actuators if name == "oled" else self.sensors
                states[name].update(status="disabled", note="Config.py 中未启用")
                if name == "body_temperature":
                    states[name].update(status="unconfigured", note="人体温度真实驱动尚未接入")
        self._update_outputs()

    def _build_drivers(self, smoke_decoder):
        cfg = self.config
        from .sensors.dht11 import Dht11Driver
        from .sensors.light import LightDriver
        from .sensors.ultrasonic import UltrasonicDriver
        from .sensors.smoke import SmokeDriver
        from .sensors.mlx90614 import Mlx90614Driver
        from .actuators.oled import OledDriver
        from .sensors.current import CurrentMonitor
        from .sensors.ads1115 import Ads1115Monitor
        from .actuators.outputs import RgbDriver, SwitchDriver
        from .actuators.servo import ServoDriver
        from .actuators.stepper import StepperDriver
        constructors = {
            "dht11": lambda: Dht11Driver(self.gpio, cfg.dht11, self._stop),
            "ultrasonic": lambda: UltrasonicDriver(self.gpio, cfg.ultrasonic),
            "bh1750": lambda: LightDriver(self.i2c, cfg.light),
            "smoke": lambda: SmokeDriver(cfg.smoke, decoder=smoke_decoder),
            "oled": lambda: OledDriver(self.i2c, cfg.oled),
        }
        sensors = {name: factory() for name, factory in constructors.items() if getattr(cfg, READER_CONFIG_NAMES[name]).enabled}
        if cfg.body_temperature.enabled and cfg.body_temperature.model == "mlx90614":
            sensors["body_temperature"] = Mlx90614Driver(self.i2c, cfg.body_temperature)
        outputs = {name: SwitchDriver(self.gpio, getattr(cfg, name)) for name in ("lock", "fan", "buzzer") if getattr(cfg, name).enabled}
        if cfg.rgb.enabled:
            outputs["rgb"] = RgbDriver(self.gpio, cfg.rgb)
        if cfg.servo.enabled:
            outputs["servo"] = ServoDriver(self.gpio, cfg.servo, cfg.thread_join_timeout)
        if cfg.stepper.enabled:
            outputs["stepper"] = StepperDriver(self.gpio, cfg.stepper, cfg.thread_join_timeout)
        self.current_monitor = CurrentMonitor(self.i2c, cfg.current)
        self.adc_monitor = Ads1115Monitor(self.i2c, cfg.ads1115)
        return sensors, outputs

    def start(self):
        with self._control_lock:
            if self._started:
                return
            self._init_errors.clear()
            self._stop.clear()
            try:
                if self.config.gpio_devices():
                    self.gpio.open()
                for name, driver in self._outputs.items():
                    driver.start()
                    self._ready_outputs.add(name)
                for name, driver in self._readers.items():
                    try:
                        if hasattr(driver, "start"):
                            driver.start()
                    except Exception as exc:
                        self._init_errors.append(f"{name}: {exc}")
                self._started = True
                for name in self._readers:
                    worker = threading.Thread(target=self._sensor_loop, args=(name,), name="sensor-" + name, daemon=True)
                    self._threads.append(worker)
                    worker.start()
            except Exception:
                try:
                    self.stop()
                except Exception as cleanup_error:
                    self._init_errors.append(f"rollback: {cleanup_error}")
                raise

    def stop(self):
        with self._control_lock:
            self._stop.set()
            self._pintest_mode = False
            self._started = False
            errors = []
            for name in tuple(self._ready_outputs):
                driver = self._outputs[name]
                try:
                    (driver.close if hasattr(driver, "close") else driver.stop)()
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
            for worker in self._threads:
                worker.join(self.config.thread_join_timeout)
            if any(worker.is_alive() for worker in self._threads):
                raise RuntimeError("采集线程未结束，保留硬件资源以避免线程访问已关闭连接")
            with self._sampling_condition:
                if not self._sampling_condition.wait_for(lambda: self._active_reads == 0, self.config.thread_join_timeout):
                    raise RuntimeError("诊断采样未结束，硬件资源保持打开")
            self._threads.clear()
            self._ready_outputs.clear()
            for name, driver in self._readers.items():
                try:
                    if hasattr(driver, "close"):
                        driver.close()
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
            for resource in (self.i2c, self.gpio):
                try:
                    resource.close()
                except Exception as exc:
                    errors.append(str(exc))
            self._update_outputs()
            if errors:
                raise RuntimeError("设备关闭失败: " + "; ".join(errors))

    def _sensor_loop(self, name):
        interval = getattr(self.config, READER_CONFIG_NAMES[name]).interval
        while not self._stop.is_set():
            if not self._pintest_mode:
                self.poll_sensor(name)
                if name == "oled":
                    self._update_oled()
            if self._stop.wait(interval):
                break

    def poll_sensor(self, name):
        states = self.actuators if name == "oled" else self.sensors
        if name not in self._readers:
            return deepcopy(states[name])
        with self._sampling_condition:
            if self._pintest_mode or self._stop.is_set():
                return deepcopy(states[name])
            self._active_reads += 1
        try:
            try:
                state = self._readers[name].read()
                online = state.get("online", True) and state.get("ok", True)
                status = "normal" if online else "error"
                note = "" if online else "本次采样无有效数据"
                if name == "smoke" and state.get("protocol") == "ffaa":
                    status, note = state["status"], state["note"]
                elif name == "smoke" and online and state.get("percent") is None:
                    status = "unknown" if state.get("decoder_configured") else "unconfigured"
                    note = "串口收到字节，尚未取得经过协议校验的测量值"
                state.update(status=status, note=note)
                state.setdefault("sample_time", time.time())
            except Exception as exc:
                state = {**READER_EMPTY_VALUES[name], "status": "error", "note": str(exc), "sample_time": time.time()}
        finally:
            with self._sampling_condition:
                self._active_reads -= 1
                self._sampling_condition.notify_all()
        with self._lock:
            states[name] = state
            return deepcopy(state)

    def _update_oled(self):
        state = self.snapshot()["sensors"]
        dht = state["dht11"]
        lines = [
            f"T:{dht.get('temp')} H:{dht.get('humidity')}",
            f"Dist:{state['ultrasonic'].get('distance')}cm",
            f"Lux:{state['bh1750'].get('lux')}",
            f"Smoke:{'OK' if state['smoke'].get('online') else '--'}",
        ]
        try:
            self._readers["oled"].show_lines(lines)
        except Exception as exc:
            with self._lock:
                self.actuators["oled"].update(ok=False, status="error", note=str(exc))

    def _update_outputs(self):
        with self._lock:
            for name, driver in self._outputs.items():
                state = driver.snapshot()
                if state.get("status") is None:
                    state.pop("status", None)
                    if not state.get("note"):
                        state.pop("note", None)
                self.actuators[name].update(state)
            for name in ACTUATORS:
                if name not in self._outputs:
                    self.actuators[name].update(status="disabled", note="Config.py 中未启用")

    def snapshot(self):
        self._update_outputs()
        with self._lock:
            return {"meta": {"mode": self.mode, "version": self.version, "time": time.time(),
                             "init_errors": list(self._init_errors), "pins": deepcopy(self.pinmap)},
                    "sensors": deepcopy(self.sensors), "actuators": deepcopy(self.actuators)}

    def control(self, name, **kwargs):
        with self._control_lock:
            if not self._started:
                raise RuntimeError("设备服务尚未启动")
            if self._pintest_mode:
                raise RuntimeError("引脚测试期间禁止正常控制")
            if name not in self._outputs:
                raise ValueError(f"执行器未启用或不存在: {name}")
            try:
                result = self._outputs[name].set(**kwargs)
            except Exception as exc:
                with self._lock:
                    self.actuators[name].update(status="error", note=str(exc), control_error=str(exc))
                raise
            self._update_outputs()
            with self._lock:
                self.actuators[name].update(status=None, note="", current_ma=None, control_error="")
            return result

    def emergency_stop(self, resume_sampling=True):
        with self._control_lock:
            was_pintest = self._pintest_mode
            errors = {}
            for name in tuple(self._ready_outputs):
                driver = self._outputs[name]
                try:
                    driver.stop()
                except Exception as exc:
                    errors[name] = str(exc)
            if self._started and was_pintest:
                # 输出设备用自己的有效电平停止；其他测试输出强制归低。
                actuator_pins = {getattr(getattr(self.config, name), "pin", None) for name in ("buzzer", "fan", "lock", "servo")}
                actuator_pins.update(self.config.rgb.pins + self.config.stepper.pins)
                for pin in set(self.pintest_out) - actuator_pins:
                    try:
                        self.gpio.setup_output(pin, 0)
                    except Exception as exc:
                        errors[f"pin-{pin}"] = str(exc)
            self._pintest_levels.clear()
            self._update_outputs()
            self._pintest_mode = not resume_sampling
            return {"ok": not errors, "errors": errors}

    def stop_actuator(self, name):
        """只停止指定执行器，使用驱动自己的关闭电平和资源锁。"""
        with self._control_lock:
            if not self._started:
                raise RuntimeError("设备服务尚未启动")
            if self._pintest_mode:
                raise RuntimeError("引脚测试期间禁止正常控制")
            if name not in self._outputs:
                raise ValueError(f"执行器未启用或不存在: {name}")
            try:
                self._outputs[name].stop()
            except Exception as exc:
                with self._lock:
                    self.actuators[name].update(status="error", note=str(exc), control_error=str(exc))
                raise
            self._update_outputs()
            with self._lock:
                self.actuators[name].update(status=None, note="", current_ma=None, control_error="")
                return deepcopy(self.actuators[name])

    def record_actuator_diagnostic(self, name, result):
        with self._lock:
            self.actuators[name].update(
                current_ma=result.get("current_ma"), status=result["status"],
                note=result.get("note", ""),
            )

    def actuator_state(self, name):
        """供状态检测读取命令和驱动状态，不执行控制动作。"""
        with self._control_lock:
            return self.snapshot()["actuators"][name]

    def actuator_output_levels(self, name):
        """读取静态输出的实际 GPIO 电平；PWM/步进不使用此比较。"""
        with self._control_lock:
            config = getattr(self.config, name)
            pins = config.pins if name == "rgb" else (config.pin,)
            return {pin: self.gpio.read(pin) for pin in pins}

    def pintest_on(self):
        if not self._started:
            raise RuntimeError("设备服务尚未启动")
        with self._control_lock:
            result = self.emergency_stop()
            if not result["ok"]:
                return result
            with self._sampling_condition:
                self._pintest_mode = True
                if not self._sampling_condition.wait_for(lambda: self._active_reads == 0, self.config.thread_join_timeout):
                    return {"mode": "blocked", "ok": False, "error": "采集尚未结束，无法进入引脚测试"}
            return {"mode": "on", "ok": True}

    def pintest_off(self):
        with self._control_lock:
            result = self.emergency_stop(resume_sampling=False)
            if not result["ok"]:
                return result
            if self._started:
                for driver in self._outputs.values():
                    if hasattr(driver, "start") and not hasattr(driver, "_thread"):
                        driver.start()
                if self.config.ultrasonic.enabled:
                    self._readers["ultrasonic"].start()
            self._pintest_mode = False
            return {"mode": "off", "ok": True}

    def pintest_set(self, pin, level):
        with self._control_lock:
            pin, level = int(pin), int(bool(int(level)))
            if not self._pintest_mode:
                return {"error": "请先打开引脚测试模式", "pin": pin}
            if pin not in self.pintest_out:
                return {"error": "该脚不是允许测试的输出脚", "pin": pin}
            try:
                self.gpio.setup_output(pin, level)
                self._pintest_levels[pin] = level
                return {"pin": pin, "level": level, "name": self.pintest_out[pin]}
            except Exception as exc:
                return {"error": str(exc), "pin": pin}

    def pintest_status(self):
        pins = [{"pin": pin, "name": name, "dir": "out", "level": self._pintest_levels.get(pin, 0)} for pin, name in self.pintest_out.items()]
        pins += [{"pin": pin, "name": name, "dir": "in", "level": None} for pin, name in self.pintest_in.items()]
        return {"mode": "on" if self._pintest_mode else "off", "pins": sorted(pins, key=lambda item: item["pin"]), "note": "I2C/UART 引脚不做输出测试"}
