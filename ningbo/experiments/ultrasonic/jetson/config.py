"""ultrasonic hardware and model defaults."""
DANGER_DISTANCE_CM = 5.0
CAUTION_DISTANCE_CM = 8.0

from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


@dataclass(frozen=True)
class UltrasonicConfig:
    enabled: bool = True
    near_buzzer_enabled: bool = True  # 距离小于 rgb_feedback.distance_danger_cm 时鸣响。
    trig_pin: int = 32
    echo_pin: int = 33
    interval: float = 1.0
    settle_seconds: float = 0.000010
    trigger_seconds: float = 0.000012
    rise_timeout_seconds: float = 0.030
    fall_timeout_seconds: float = 0.040
    minimum_pulse_us: float = 100.0
    maximum_distance_cm: float = 400.0
    sound_speed_cm_per_second: float = 34300.0


@dataclass(frozen=True)
class BuzzerConfig:
    pin: int = 40
    active_high: bool = False
    enabled: bool = True
    diagnostic_full_scale_volts: float = 0.256
    diagnostic_settle_seconds: float = 0.25
    diagnostic_sample_count: int = 15
    diagnostic_minimum_delta_volts: float = 0.010
    diagnostic_maximum_baseline_drift_volts: float = 0.002

import math


def validate(self):
    buzzer = self.buzzer
    if buzzer.diagnostic_full_scale_volts not in (6.144, 4.096, 2.048, 1.024, 0.512, 0.256):
        raise ValueError("蜂鸣器状态检测 ADC 量程无效")
    if (isinstance(buzzer.diagnostic_sample_count, bool)
            or not isinstance(buzzer.diagnostic_sample_count, int)
            or not 3 <= buzzer.diagnostic_sample_count <= 31):
        raise ValueError("蜂鸣器状态检测采样次数必须为 3~31")
    if (not math.isfinite(buzzer.diagnostic_settle_seconds)
            or not 0 < buzzer.diagnostic_settle_seconds <= 1
            or not math.isfinite(buzzer.diagnostic_minimum_delta_volts)
            or not 0 < buzzer.diagnostic_minimum_delta_volts < buzzer.diagnostic_full_scale_volts
            or not math.isfinite(buzzer.diagnostic_maximum_baseline_drift_volts)
            or not 0 < buzzer.diagnostic_maximum_baseline_drift_volts < buzzer.diagnostic_full_scale_volts):
        raise ValueError("蜂鸣器状态检测稳定时间或电压差阈值无效")
    for name in ('ultrasonic',):
        value = getattr(self, name).interval
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name}: sampling interval must be positive")
    timings = (self.ultrasonic.settle_seconds, self.ultrasonic.trigger_seconds, self.ultrasonic.rise_timeout_seconds, self.ultrasonic.fall_timeout_seconds, self.ultrasonic.sound_speed_cm_per_second,)
    if any(not math.isfinite(value) or value <= 0 for value in timings):
        raise ValueError("Timing, frequency and scale values must be finite and positive")
    if not isinstance(self.ultrasonic.near_buzzer_enabled, bool):
        raise ValueError("超声波 near_buzzer_enabled 必须是布尔值")

    feedback = self.rgb_feedback
    if (not math.isfinite(feedback.distance_danger_cm) or not math.isfinite(feedback.distance_caution_cm)
            or not 0 < feedback.distance_danger_cm < feedback.distance_caution_cm
            or feedback.distance_caution_cm > self.ultrasonic.maximum_distance_cm):
        raise ValueError("RGB 距离阈值须满足 0 < danger < caution ≤ 超声波最大距离")


def pin_assignments(config):
    pins = [(config.buzzer.pin, "buzzer")] if config.buzzer.enabled else []
    if config.ultrasonic.enabled:
        pins.extend(((config.ultrasonic.trig_pin, "ultrasonic.trig"), (config.ultrasonic.echo_pin, "ultrasonic.echo")))
    return pins
