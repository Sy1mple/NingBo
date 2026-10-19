"""environment hardware and model defaults."""
from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


@dataclass(frozen=True)
class Dht11Config:
    enabled: bool = True
    pin: int = 37  # 已在 Xavier NX 实机确认数据线位于 BOARD 37。
    backend: str = "gpiod_native"
    native_helper: str = "experiments/environment/jetson/native/dht11_gpiod"
    gpio_chip: str = "/dev/gpiochip1"
    line_offset: int = 151  # BOARD 37 / PY.02 在当前 Xavier NX 系统上的 GPIO line。
    native_timeout_seconds: float = 0.5
    interval: float = 2.0
    attempts: int = 5
    retry_interval: float = 1.1
    start_low_seconds: float = 0.020
    edge_timeout_seconds: float = 0.005
    one_bit_threshold_us: float = 50.0
    temperature_range: tuple = (0.0, 60.0)
    humidity_range: tuple = (0.0, 100.0)


@dataclass(frozen=True)
class LightConfig:
    enabled: bool = True
    address: int = 0x23
    interval: float = 1.0
    measurement_seconds: float = 0.180
    counts_per_lux: float = 1.2
    display_full_scale_lux: float = 1000.0  # 沿用合并包的示例百分比换算

import math


def validate(self):
    for name in ('dht11', 'light'):
        value = getattr(self, name).interval
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name}: sampling interval must be positive")
    timings = (self.dht11.start_low_seconds, self.dht11.edge_timeout_seconds, self.dht11.native_timeout_seconds, self.light.measurement_seconds, self.light.counts_per_lux, self.light.display_full_scale_lux,)
    if any(not math.isfinite(value) or value <= 0 for value in timings):
        raise ValueError("Timing, frequency and scale values must be finite and positive")
    if self.dht11.interval < 1 or self.dht11.retry_interval < 1 or self.dht11.attempts < 1:
        raise ValueError("DHT11 读取/重试间隔至少 1 秒且尝试次数至少 1")
    if self.dht11.backend not in ("gpiod_native", "python_poll"):
        raise ValueError("DHT11 backend 仅支持 gpiod_native 或 python_poll")
    if (not self.dht11.native_helper or not self.dht11.gpio_chip.startswith("/dev/gpiochip")
            or isinstance(self.dht11.line_offset, bool) or self.dht11.line_offset < 0):
        raise ValueError("DHT11 原生采样器路径、gpiochip 或 line offset 无效")


def pin_assignments(config):
    if config.dht11.enabled:
        return [(config.dht11.pin, "dht11")]
    return []
