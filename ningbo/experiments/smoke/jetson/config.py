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

import math


def validate(self):
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
            or not math.isfinite(self.smoke.stale_after_seconds) or self.smoke.stale_after_seconds <= 0):
        raise ValueError("烟雾读缓冲或有效数据超时无效")
    if self.smoke.baudrate <= 0 or not self.smoke.port or self.smoke.read_size <= 0 or self.smoke.timeout <= 0:
        raise ValueError("烟雾串口、波特率、读缓冲或超时无效")
    if self.smoke.bytesize not in (5, 6, 7, 8) or self.smoke.parity not in ("N", "E", "O", "M", "S") or self.smoke.stopbits not in (1, 1.5, 2):
        raise ValueError("烟雾串口数据格式无效")
