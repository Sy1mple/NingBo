"""body_temperature hardware and model defaults."""
HIGH_TEMPERATURE_C = 37.5
FEEDBACK_VALID_RANGE = (35.0, 45.0)

from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


@dataclass(frozen=True)
class BodyTemperatureConfig:
    enabled: bool = True
    model: str = "mlx90614"  # GY-906，与 BH1750 共用 I2C8。
    address: int = 0x5A
    interval: float = 1.0
    temperature_range: tuple = (-70.0, 380.0)
    ambient_range: tuple = (-40.0, 125.0)
    display_range: tuple = (0.0, 100.0)  # 屏幕显示范围；RGB 的人体温度范围单独判断。

import math


def validate(self):
    for name in ('body_temperature',):
        value = getattr(self, name).interval
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name}: sampling interval must be positive")
    if self.body_temperature.enabled and (not self.body_temperature.model or self.body_temperature.model == "unconfigured"):
        raise ValueError("启用人体温度前需填写 mlx90614 型号，或为 DeviceService 注入真实驱动")
    body = self.body_temperature
    if isinstance(body.address, bool) or not isinstance(body.address, int) or not 0x08 <= body.address <= 0x77:
        raise ValueError("人体温度 I2C 地址必须是有效的 7 位地址")
    for label, limits in (("测量", body.temperature_range), ("环境", body.ambient_range), ("显示", body.display_range)):
        if (len(limits) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in limits)
                or not limits[0] < limits[1]):
            raise ValueError(f"人体温度{label}范围无效")
    if not 0 <= body.display_range[0] < body.display_range[1] <= 327.67:
        raise ValueError("人体温度显示范围超出屏幕 Word 范围")

    feedback = self.rgb_feedback
    low, high = feedback.body_temperature_valid_range
    if (any(not math.isfinite(value) for value in (low, high, feedback.body_temperature_high_c))
            or not low <= feedback.body_temperature_high_c < high):
        raise ValueError("RGB 体温有效范围和高温阈值无效")
