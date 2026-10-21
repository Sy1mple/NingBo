"""smoke hardware and model defaults."""
from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


@dataclass(frozen=True)
class SmokeConfig:
    enabled: bool = True
    port: str = "/dev/ttyTHS0"  # Xavier NX：BOARD 8=TX、10=RX。
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    timeout: float = 0.25
    interval: float = 0.2
    read_size: int = 256
    protocol: str = "ffaa"  # FF AA 浓度高字节 浓度低字节 3C；单位待型号确认。
    stale_after_seconds: float = 5.0
    diagnostic_timeout_seconds: float = 30.0  # 状态检测整轮等待上限；5 秒仅表示旧数据失效。
    diagnostic_reconnect_interval_seconds: float = 5.0  # 检测中无帧时重建 UART，不结束检测。
    # 2026-10-21 正常空气 40 帧：1644~1695，中位数 1655。
    # 固定参考，不随开始按钮归零。相对演示指数，不是气体浓度百分比。
    display_baseline_raw: int = 1655
    display_normal_value: int = 20
    display_full_scale_raw: int = 3310  # 参考值翻倍时显示 100。
    alarm_threshold: int = 80  # 与屏幕相同的演示百分比。
    alarm_period_seconds: float = 3.0
    alarm_on_seconds: float = 0.4

import math


def validate(self):
    cfg = self.smoke
    if (isinstance(cfg.alarm_threshold, bool) or not isinstance(cfg.alarm_threshold, int)
            or not 0 <= cfg.alarm_threshold <= 100
            or isinstance(cfg.display_full_scale_raw, bool)
            or not isinstance(cfg.display_full_scale_raw, int)
            or not 1 <= cfg.display_full_scale_raw <= 65535
            or isinstance(cfg.display_baseline_raw, bool)
            or not isinstance(cfg.display_baseline_raw, int)
            or not 0 <= cfg.display_baseline_raw < cfg.display_full_scale_raw
            or isinstance(cfg.display_normal_value, bool)
            or not isinstance(cfg.display_normal_value, int)
            or not 0 <= cfg.display_normal_value < cfg.alarm_threshold
            or not math.isfinite(cfg.alarm_period_seconds)
            or not math.isfinite(cfg.alarm_on_seconds)
            or not 0 < cfg.alarm_on_seconds < cfg.alarm_period_seconds):
        raise ValueError("烟雾报警阈值或鸣响周期无效")
    for name in ('smoke',):
        value = getattr(self, name).interval
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name}: sampling interval must be positive")
    if self.smoke.protocol not in ("raw", "ffaa"):
        raise ValueError("烟雾协议必须为 raw 或 ffaa")
    if self.smoke.protocol == "ffaa" and (self.smoke.bytesize, self.smoke.parity, self.smoke.stopbits) != (8, "N", 1):
        raise ValueError("FFAA 烟雾协议使用 8N1")
    if (isinstance(self.smoke.read_size, bool) or not isinstance(self.smoke.read_size, int)
            or not 5 <= self.smoke.read_size <= 4096
            or not math.isfinite(self.smoke.stale_after_seconds) or self.smoke.stale_after_seconds <= 0
            or not math.isfinite(self.smoke.diagnostic_timeout_seconds)
            or self.smoke.diagnostic_timeout_seconds <= self.smoke.stale_after_seconds
            or not math.isfinite(self.smoke.diagnostic_reconnect_interval_seconds)
            or not 0 < self.smoke.diagnostic_reconnect_interval_seconds < self.smoke.diagnostic_timeout_seconds):
        raise ValueError("烟雾读缓冲或有效数据超时无效")
    if self.smoke.baudrate <= 0 or not self.smoke.port or self.smoke.read_size <= 0 or self.smoke.timeout <= 0:
        raise ValueError("烟雾串口、波特率、读缓冲或超时无效")
    if self.smoke.bytesize not in (5, 6, 7, 8) or self.smoke.parity not in ("N", "E", "O", "M", "S") or self.smoke.stopbits not in (1, 1.5, 2):
        raise ValueError("烟雾串口数据格式无效")
