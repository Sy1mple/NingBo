"""DHT11 位时序驱动，GPIO 引脚和时序来自 config.py。"""
from pathlib import Path
from paths import PROJECT_ROOT
import subprocess
import threading
import time


class Dht11Driver:
    def __init__(self, gpio, config, stop_event=None):
        self.gpio = gpio
        self.config = config
        self.lock = threading.Lock()
        self.stop_event = stop_event
        self._last_attempt = float("-inf")
        self._last_value = None

    def _wait_level(self, level):
        until = time.perf_counter() + self.config.edge_timeout_seconds
        while self.gpio.read(self.config.pin) != level:
            if time.perf_counter() >= until:
                raise TimeoutError("DHT11 无应答或数据边沿超时")

    def _read_python_once(self):
        cfg = self.config
        self.gpio.setup_output(cfg.pin, 0)
        time.sleep(cfg.start_low_seconds)
        self.gpio.setup_input(cfg.pin, pull_up=True)
        self._wait_level(0)
        self._wait_level(1)
        self._wait_level(0)
        values = [0] * 5
        for index in range(40):
            self._wait_level(1)
            start = time.perf_counter()
            self._wait_level(0)
            bit = int((time.perf_counter() - start) * 1e6 > cfg.one_bit_threshold_us)
            values[index // 8] = (values[index // 8] << 1) | bit
        if sum(values[:4]) & 0xFF != values[4]:
            raise ValueError("DHT11 校验错误")
        temp = values[2] + values[3] * 0.1
        humidity = values[0] + values[1] * 0.1
        if not cfg.temperature_range[0] <= temp <= cfg.temperature_range[1] or not cfg.humidity_range[0] <= humidity <= cfg.humidity_range[1]:
            raise ValueError("DHT11 数值超出配置范围")
        return {"temp": round(temp, 1), "humidity": round(humidity, 1)}

    def _read_native_once(self):
        cfg = self.config
        helper = Path(cfg.native_helper)
        if not helper.is_absolute():
            helper = PROJECT_ROOT / helper
        if not helper.is_file():
            raise RuntimeError(f"DHT11 原生采样器不存在: {helper}")
        try:
            result = subprocess.run(
                (str(helper), cfg.gpio_chip, str(cfg.line_offset)),
                capture_output=True,
                text=True,
                timeout=cfg.native_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("DHT11 原生采样超时") from exc
        if result.returncode:
            detail = result.stderr.strip() or f"退出码 {result.returncode}"
            raise RuntimeError(f"DHT11 原生采样失败: {detail}")
        fields = result.stdout.strip().split()
        if len(fields) != 2:
            raise RuntimeError(f"DHT11 原生采样输出无效: {result.stdout.strip()!r}")
        try:
            temp, humidity = map(float, fields)
        except ValueError as exc:
            raise RuntimeError(f"DHT11 原生采样输出无效: {result.stdout.strip()!r}") from exc
        if (not cfg.temperature_range[0] <= temp <= cfg.temperature_range[1]
                or not cfg.humidity_range[0] <= humidity <= cfg.humidity_range[1]):
            raise ValueError("DHT11 数值超出配置范围")
        return {"temp": round(temp, 1), "humidity": round(humidity, 1)}

    def _read_once(self):
        if self.config.backend == "gpiod_native":
            return self._read_native_once()
        return self._read_python_once()

    def read(self):
        with self.lock:
            remaining = self.config.interval - (time.monotonic() - self._last_attempt)
            if remaining > 0:
                if self._last_value is not None:
                    return dict(self._last_value)
                # A failed background read leaves no cached value. A button
                # read must wait for the next permitted attempt, not report
                # this normal cooldown as a sensor fault. Keep the same lock
                # so background and diagnostic readers cannot race the GPIO.
                if self.stop_event is None:
                    time.sleep(remaining)
                elif self.stop_event.wait(remaining):
                    raise RuntimeError("DHT11 采样已取消")
            last_error = None
            for attempt in range(self.config.attempts):
                if attempt:
                    if self.stop_event is None:
                        time.sleep(self.config.retry_interval)
                    elif self.stop_event.wait(self.config.retry_interval):
                        raise RuntimeError("DHT11 采样已取消")
                self._last_attempt = time.monotonic()
                try:
                    self._last_value = self._read_once()
                    return dict(self._last_value)
                except (OSError, RuntimeError, ValueError) as exc:
                    self._last_value = None
                    last_error = exc
            raise RuntimeError(f"DHT11 读取失败: {last_error}") from last_error
