"""Configuration owned by hardware/rgb."""
from dataclasses import dataclass, field
import math
from experiments.body_temperature.jetson.config import HIGH_TEMPERATURE_C, FEEDBACK_VALID_RANGE
from experiments.ultrasonic.jetson.config import DANGER_DISTANCE_CM, CAUTION_DISTANCE_CM

@dataclass(frozen=True)
class RgbConfig:
    pins: tuple = (38, 35, 12)  # 红、绿、蓝；用户确认的 BOARD 物理引脚。
    active_high: bool = True
    enabled: bool = True
    # 2026-10-19: 0x48/AIN3, four normal-load calibration cycles.
    # Use per-test OFF baselines; absolute readings contain a negative offset.
    diagnostic_full_scale_volts: float = 0.256
    diagnostic_settle_seconds: float = 0.25
    diagnostic_sample_count: int = 9
    diagnostic_maximum_baseline_drift_volts: float = 0.000060
    diagnostic_minimum_delta_volts: dict = field(default_factory=lambda: {
        "red": 0.000180, "green": 0.000120, "blue": 0.000090,
    })


@dataclass(frozen=True)
class RgbFeedbackConfig:
    enabled: bool = True
    # 教学演示规则；型号/测温部位确认后按真实模块标定。
    body_temperature_high_c: float = HIGH_TEMPERATURE_C
    body_temperature_valid_range: tuple = FEEDBACK_VALID_RANGE
    distance_danger_cm: float = DANGER_DISTANCE_CM    # 红灯 + 蜂鸣
    distance_caution_cm: float = CAUTION_DISTANCE_CM   # 蓝 / 绿分界；5≤d<8 蓝，d≥8 绿
    sample_max_age_seconds: float = 5.0
    waiting_color: str = "blue"


def validate(self):
    if len(self.rgb.pins) != 3:
        raise ValueError("RGB needs three pins")
    rgb = self.rgb
    if rgb.diagnostic_full_scale_volts not in (6.144, 4.096, 2.048, 1.024, 0.512, 0.256):
        raise ValueError("RGB 状态检测 ADC 量程无效")
    if (isinstance(rgb.diagnostic_sample_count, bool)
            or not isinstance(rgb.diagnostic_sample_count, int)
            or not 3 <= rgb.diagnostic_sample_count <= 31):
        raise ValueError("RGB 状态检测采样次数必须为 3~31")
    if (not math.isfinite(rgb.diagnostic_settle_seconds)
            or not 0 < rgb.diagnostic_settle_seconds <= 1
            or not math.isfinite(rgb.diagnostic_maximum_baseline_drift_volts)
            or not 0 < rgb.diagnostic_maximum_baseline_drift_volts < rgb.diagnostic_full_scale_volts):
        raise ValueError("RGB 状态检测稳定时间或基线漂移阈值无效")
    if (set(rgb.diagnostic_minimum_delta_volts) != {"red", "green", "blue"}
            or any(not math.isfinite(v) or not 0 < v < rgb.diagnostic_full_scale_volts
                   for v in rgb.diagnostic_minimum_delta_volts.values())):
        raise ValueError("RGB 三色状态检测差值阈值无效")
    feedback = self.rgb_feedback
    if not math.isfinite(feedback.sample_max_age_seconds) or feedback.sample_max_age_seconds <= 0:
        raise ValueError("RGB 读数有效期必须为有限正数")
    if feedback.waiting_color not in ("blue", "off"):
        raise ValueError("RGB 等待颜色只能为 blue 或 off")


def pin_assignments(config):
    return [(pin, f"rgb.{i}") for i, pin in enumerate(config.rgb.pins)] if config.rgb.enabled else []
