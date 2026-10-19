"""Configuration owned by system/diagnostics/jetson."""
from dataclasses import dataclass, field
import math

@dataclass(frozen=True)
class ActuatorDiagnosticConfig:
    sample_count: int = 3
    sample_interval: float = 0.02
    gpio_readback: bool = True


def validate(self):
    policy = self.actuator_diagnostics
    if isinstance(policy.sample_count, bool) or not isinstance(policy.sample_count, int) or not 1 <= policy.sample_count <= 31:
        raise ValueError("执行器电流采样次数必须为 1~31 的整数")
    if not math.isfinite(policy.sample_interval) or policy.sample_interval < 0:
        raise ValueError("执行器电流采样间隔必须是有限非负数")
    if policy.sample_count * (policy.sample_interval + self.current.conversion_seconds) >= self.thread_join_timeout:
        raise ValueError("执行器电流采样耗时不能超过线程关闭超时")
