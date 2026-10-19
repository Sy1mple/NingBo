"""硬件 PWM 舵机驱动；内核持续输出脉冲，不使用 Python 翻转 GPIO。"""
import math
import threading
import time
import logging

LOGGER = logging.getLogger(__name__)


class ServoDriver:
    def __init__(self, gpio, config, join_timeout=5.0):
        self.gpio = gpio
        self.config = config
        self.join_timeout = join_timeout
        self.angle = config.default_angle
        self.on = False
        self.error = ""
        self.lock = threading.RLock()
        self._pwm = None
        self.sweeping = False
        self._sequence_index = 0
        self._next_move = 0.0
        self._closing = threading.Event()
        self._thread = None

    def start(self):
        with self.lock:
            if self._pwm is None:
                self._pwm = self.gpio.create_pwm(self.config.pin, self.config.frequency_hz)
                self.on = False
                self.error = ""
                self._closing.clear()
                self._thread = threading.Thread(target=self._run_sweep, name="servo-angle-update", daemon=True)
                try:
                    self._thread.start()
                except Exception:
                    self.gpio.release_pwm(self.config.pin)
                    self._pwm = None
                    raise

    def pulse_width_us(self, angle):
        fraction = (angle - self.config.min_angle) / (self.config.max_angle - self.config.min_angle)
        return self.config.minimum_pulse_us + fraction * (self.config.maximum_pulse_us - self.config.minimum_pulse_us)

    def set(self, angle=None, sweep=False):
        if sweep:
            with self.lock:
                self.sweeping = False
                result = self.set(self.config.sweep_min_angle)
                self._sequence_index = 0
                self._next_move = time.monotonic() + self.config.sweep_interval_seconds
                self.sweeping = True
                return {**result, "sweeping": True}
        if isinstance(angle, bool) or not math.isfinite(float(angle)):
            raise ValueError("舵机角度必须是有限数字")
        with self.lock:
            if self._pwm is None:
                raise RuntimeError("舵机硬件 PWM 尚未初始化")
            target = max(self.config.min_angle, min(self.config.max_angle, float(angle)))
            pulse = self.pulse_width_us(target)
            duty = pulse * self.config.frequency_hz / 10000.0
            try:
                # 与已转动成功的独立测试相同：每个位置直接调用 start。
                self._pwm.start(duty)
            except Exception as exc:
                self.on = False
                self.sweeping = False
                self.error = str(exc)
                try:
                    self._pwm.stop()
                except Exception as stop_error:
                    self.error += f"; PWM 停止失败: {stop_error}"
                raise
            self.angle = target
            self.sweeping = False
            self.on = True
            self.error = ""
            LOGGER.info("舵机下发：角度=%g° 脉宽=%g us 占空比=%g%%", target, pulse, duty)
            return {"angle": self.angle, "pulse_us": pulse, "backend": "hardware_pwm"}

    def stop(self):
        with self.lock:
            self.sweeping = False
            try:
                if self._pwm is not None:
                    self._pwm.stop()
            except Exception as exc:
                self.error = str(exc)
                raise
            self.on = False

    def close(self):
        self._closing.set()
        self.stop()
        if self._thread:
            self._thread.join(self.join_timeout)
            if self._thread.is_alive():
                raise RuntimeError("舵机角度更新线程未结束")
            self._thread = None
        with self.lock:
            if self._pwm is not None:
                self.gpio.release_pwm(self.config.pin)
                self._pwm = None

    def _advance_sweep(self):
        with self.lock:
            if not self.sweeping or time.monotonic() < self._next_move:
                return
            angles = (self.config.sweep_min_angle, self.config.sweep_max_angle, self.config.default_angle)
            self._sequence_index = (self._sequence_index + 1) % len(angles)
            target = angles[self._sequence_index]
            self.set(target)
            self.sweeping = True
            self._next_move = time.monotonic() + self.config.sweep_interval_seconds

    def _run_sweep(self):
        while not self._closing.wait(0.05):
            try:
                self._advance_sweep()
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
                    self.sweeping = False

    def snapshot(self):
        with self.lock:
            observed = None
            read_error = ""
            if self._pwm is not None:
                try:
                    observed = self.gpio.pwm_state(self._pwm)
                except Exception as exc:
                    read_error = str(exc)
            return {"angle": self.angle, "on": self.on, "backend": "hardware_pwm",
                    "sweeping": self.sweeping, "worker_alive": bool(self._thread and self._thread.is_alive()),
                    "pwm_ready": self._pwm is not None, "pwm_state": observed,
                    "pwm_read_error": read_error, "pulse_us": self.pulse_width_us(self.angle),
                    "period_ns": int(1e9 / self.config.frequency_hz),
                    "note": self.error, "driver_error": self.error,
                    "status": "error" if self.error else None}
