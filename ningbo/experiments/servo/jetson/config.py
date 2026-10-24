"""servo hardware and model defaults."""
from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


@dataclass(frozen=True)
class ServoConfig:
    pin: int = 15
    enabled: bool = True
    min_angle: float = 0.0
    max_angle: float = 180.0
    default_angle: float = 90.0  # 未启动时的内部初始值；屏幕启动使用下方往返范围。
    minimum_pulse_us: float = 500.0
    maximum_pulse_us: float = 2500.0
    frequency_hz: float = 50.0
    # 硬件 PWM：20 ms 周期；45°/90°/135°对应 1000/1500/2000 us。
    sweep_min_angle: float = 45.0
    sweep_max_angle: float = 135.0
    sweep_interval_seconds: float = 4.0  # 每个位置保持 4 秒。
    display_interval_seconds: float = 0.2
    diagnostic_full_scale_volts: float = 0.256
    diagnostic_settle_seconds: float = 0.8
    diagnostic_sample_count: int = 9
    # 2026-10-24 用户确认能转动的两轮读数：最小增量约 0.563mV，
    # 停止前后变化约 0.015mV；以 0.300mV 检查运行响应。
    diagnostic_minimum_delta_volts: float = 0.0003
    diagnostic_maximum_baseline_drift_volts: float = 0.0005

import math


def validate(self):
    timings = (self.servo.frequency_hz,)
    if any(not math.isfinite(value) or value <= 0 for value in timings):
        raise ValueError("Timing, frequency and scale values must be finite and positive")
    if not 0 <= self.servo.min_angle < self.servo.max_angle:
        raise ValueError("舵机角度范围无效")
    if not self.servo.min_angle <= self.servo.default_angle <= self.servo.max_angle:
        raise ValueError("舵机默认角度超出范围")
    if self.servo.frequency_hz <= 0 or not 0 < self.servo.minimum_pulse_us < self.servo.maximum_pulse_us < 1e6 / self.servo.frequency_hz:
        raise ValueError("舵机脉宽或频率配置无效")
    sweep = self.servo
    if sweep.diagnostic_full_scale_volts not in (6.144, 4.096, 2.048, 1.024, 0.512, 0.256):
        raise ValueError("舵机状态检测 ADC 量程无效")
    if (isinstance(sweep.diagnostic_sample_count, bool)
            or not isinstance(sweep.diagnostic_sample_count, int)
            or not 3 <= sweep.diagnostic_sample_count <= 31):
        raise ValueError("舵机状态检测采样次数必须为 3~31")
    if (not math.isfinite(sweep.diagnostic_settle_seconds)
            or not 0 < sweep.diagnostic_settle_seconds <= 2
            or not math.isfinite(sweep.diagnostic_minimum_delta_volts)
            or not 0 < sweep.diagnostic_minimum_delta_volts < sweep.diagnostic_full_scale_volts
            or not math.isfinite(sweep.diagnostic_maximum_baseline_drift_volts)
            or not 0 < sweep.diagnostic_maximum_baseline_drift_volts < sweep.diagnostic_full_scale_volts):
        raise ValueError("舵机状态检测稳定时间或电压差阈值无效")
    if (any(isinstance(v, bool) or not math.isfinite(v) for v in
            (sweep.sweep_min_angle, sweep.sweep_max_angle,
             sweep.sweep_interval_seconds, sweep.display_interval_seconds))
            or not sweep.min_angle <= sweep.sweep_min_angle < sweep.sweep_max_angle <= sweep.max_angle
            or sweep.sweep_interval_seconds <= 0 or sweep.display_interval_seconds <= 0):
        raise ValueError("舵机循环范围、停留时间或刷新周期无效")


def pin_assignments(config):
    return [(config.servo.pin, "servo")] if config.servo.enabled else []
