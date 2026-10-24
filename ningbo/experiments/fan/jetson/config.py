"""Fan pin and polarity configuration."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FanConfig:
    pin: int = 31
    active_high: bool = True
    enabled: bool = True
    diagnostic_full_scale_volts: float = 0.256
    diagnostic_on_settle_seconds: float = 1.2
    diagnostic_off_settle_seconds: float = 0.6
    diagnostic_sample_count: int = 15
    diagnostic_noise_multiplier: float = 6.0
    diagnostic_maximum_baseline_drift_volts: float = 0.005


def validate(self):
    cfg = self.fan
    if cfg.diagnostic_full_scale_volts not in (6.144, 4.096, 2.048, 1.024, 0.512, 0.256):
        raise ValueError("风扇状态检测 ADC 量程无效")
    if (isinstance(cfg.diagnostic_sample_count, bool)
            or not isinstance(cfg.diagnostic_sample_count, int)
            or not 3 <= cfg.diagnostic_sample_count <= 31):
        raise ValueError("风扇状态检测采样次数必须为 3~31")
    if any(not math.isfinite(v) or not 0 < v <= 2 for v in
           (cfg.diagnostic_on_settle_seconds, cfg.diagnostic_off_settle_seconds)):
        raise ValueError("风扇状态检测稳定时间无效")
    if (not math.isfinite(cfg.diagnostic_maximum_baseline_drift_volts)
            or not 0 < cfg.diagnostic_maximum_baseline_drift_volts < cfg.diagnostic_full_scale_volts):
        raise ValueError("风扇状态检测电压差阈值无效")
    if not math.isfinite(cfg.diagnostic_noise_multiplier) or not 3 <= cfg.diagnostic_noise_multiplier <= 12:
        raise ValueError("风扇状态检测噪声倍率无效")


def pin_assignments(config):
    return [(config.fan.pin, "fan")] if config.fan.enabled else []
