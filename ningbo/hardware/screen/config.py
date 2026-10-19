"""Configuration owned by hardware/screen."""
from dataclasses import dataclass, field
import math

@dataclass(frozen=True)
class ScreenConfig:
    port: str = "auto"  # 扫描 USB 串口并验证屏幕协议应答，避免编号/别名变化。
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    read_timeout: float = 0.5
    write_timeout: float = 1.0
    refresh_interval: float = 1.0
    event_poll_interval: float = 0.05
    auto_probe_timeout: float = 0.35
    auto_probe_attempts: int = 2


def validate(self):
    timings = (self.screen.read_timeout, self.screen.write_timeout, self.screen.refresh_interval, self.screen.event_poll_interval,)
    if any(not math.isfinite(value) or value <= 0 for value in timings):
        raise ValueError("Timing, frequency and scale values must be finite and positive")
    if not isinstance(self.screen.port, str) or not self.screen.port.strip() or self.screen.baudrate <= 0:
        raise ValueError("串口屏端口或波特率无效")
    if (isinstance(self.screen.auto_probe_timeout, bool) or not math.isfinite(self.screen.auto_probe_timeout) or not 0.05 <= self.screen.auto_probe_timeout <= 2
            or isinstance(self.screen.auto_probe_attempts, bool) or not isinstance(self.screen.auto_probe_attempts, int)
            or not 1 <= self.screen.auto_probe_attempts <= 3):
        raise ValueError("串口屏自动探测超时或重试次数无效")
    if (self.screen.bytesize, self.screen.parity, self.screen.stopbits) != (8, "N", 1):
        raise ValueError("JGUS 串口屏当前使用 8N1")
