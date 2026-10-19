"""stepper hardware and model defaults."""
from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


@dataclass(frozen=True)
class StepperConfig:
    pins: tuple = (13, 16, 18, 22)
    enabled: bool = True
    sequence: tuple = (
        (1, 0, 1, 0), (1, 0, 0, 0), (1, 1, 0, 0), (0, 1, 0, 0),
        (0, 1, 1, 0), (0, 0, 1, 0), (0, 0, 1, 1), (1, 0, 0, 1),
    )
    default_speed_ms: int = 8
    minimum_speed_ms: int = 2
    maximum_speed_ms: int = 20
    maximum_steps: int = 100000
    idle_wait: float = 0.02
    display_interval_seconds: float = 0.2
    diagnostic_full_scale_volts: float = 0.512
    diagnostic_settle_seconds: float = 0.20
    diagnostic_sample_count: int = 25
    diagnostic_steps: int = 256
    diagnostic_minimum_delta_volts: float = 0.150
    diagnostic_maximum_baseline_drift_volts: float = 0.005

import math


def validate(self):
    if len(self.stepper.pins) != 4:
        raise ValueError("Stepper needs four phase pins")
    timings = (self.stepper.idle_wait, self.stepper.display_interval_seconds)
    if any(not math.isfinite(value) or value <= 0 for value in timings):
        raise ValueError("Timing, frequency and scale values must be finite and positive")
    if not self.stepper.sequence or any(len(row) != 4 or any(x not in (0, 1) for x in row) for row in self.stepper.sequence):
        raise ValueError("步进电机相序必须由四路 0/1 电平组成")
    if not 0 < self.stepper.minimum_speed_ms <= self.stepper.default_speed_ms <= self.stepper.maximum_speed_ms:
        raise ValueError("步进速度范围无效")
    cfg = self.stepper
    if cfg.diagnostic_full_scale_volts not in (6.144, 4.096, 2.048, 1.024, 0.512, 0.256):
        raise ValueError("步进状态检测 ADC 量程无效")
    if (isinstance(cfg.diagnostic_sample_count, bool)
            or not isinstance(cfg.diagnostic_sample_count, int)
            or not 3 <= cfg.diagnostic_sample_count <= 31
            or isinstance(cfg.diagnostic_steps, bool) or not isinstance(cfg.diagnostic_steps, int)
            or not 1 <= cfg.diagnostic_steps <= cfg.maximum_steps):
        raise ValueError("步进状态检测采样次数或步数无效")
    if (not math.isfinite(cfg.diagnostic_settle_seconds)
            or not 0 < cfg.diagnostic_settle_seconds <= 1
            or not math.isfinite(cfg.diagnostic_minimum_delta_volts)
            or not 0 < cfg.diagnostic_minimum_delta_volts < cfg.diagnostic_full_scale_volts
            or not math.isfinite(cfg.diagnostic_maximum_baseline_drift_volts)
            or not 0 < cfg.diagnostic_maximum_baseline_drift_volts < cfg.diagnostic_full_scale_volts):
        raise ValueError("步进状态检测稳定时间或电压差阈值无效")
    if cfg.diagnostic_steps * cfg.default_speed_ms / 1000 <= cfg.diagnostic_settle_seconds + cfg.diagnostic_sample_count / 128 + .2:
        raise ValueError("步进状态检测步数不足以覆盖采样窗口")


def pin_assignments(config):
    return [(pin, f"stepper.{i}") for i, pin in enumerate(config.stepper.pins)] if config.stepper.enabled else []
