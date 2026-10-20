"""Configuration owned by hardware/adc."""
from dataclasses import dataclass, field
import math

@dataclass(frozen=True)
class CurrentChannel:
    address: int
    channel: int
    shunt_ohms: float
    threshold_ma: float = 30.0  # 示例阈值，各设备安装后分别标定
    max_current_ma: float = None
    off_current_ma: float = None


def _feedback_locations():
    """共用的默认采样位置；电压和电流检测不能各留一份旧接线表。"""
    return {
        "servo": (0x48, 0), "lock": (0x48, 1),
        "fan": (0x48, 2), "rgb": (0x48, 3),
        "buzzer": (0x49, 0), "stepper": (0x49, 1),
    }


def _current_channels():
    # 沿用待标定的电阻参数；统一位置不代表已确认电流换算或启用检测。
    shunts = {"lock": 1.0, "fan": 1.0, "servo": 1.0,
              "stepper": 0.5, "rgb": 10.0, "buzzer": 22.0}
    channels = {name: CurrentChannel(address, channel, shunts[name])
                for name, (address, channel) in _feedback_locations().items()}
    channels["bus5v"] = CurrentChannel(0x49, 2, 0.1)
    return channels


@dataclass(frozen=True)
class Ads1115Config:
    # 现场已在 Jetson I2C8 探测到 0x48。量程不是输入耐压。
    enabled: bool = True
    address: int = 0x48
    full_scale_volts: float = 4.096
    timeout_seconds: float = 0.2
    feedback_channels: dict = field(default_factory=lambda: {
        name: channel for name, (_, channel) in _feedback_locations().items()})
    # 用户确认：0x48 A0舵机/A1锁/A2风扇/A3指示灯；0x49 A0蜂鸣器/A1步进；ADDR 接线需进一步核实。
    feedback_addresses: dict = field(default_factory=lambda: {
        name: address for name, (address, _) in _feedback_locations().items() if address != 0x48})
    # 2026-10-19 A0 三轮复测：PWM 关闭 8.47~8.56mV，运行 12.88~12.97mV。
    # (关闭电压上限, 运行电压下限)，中间区间不作确定判断。
    # 2026-10-19 A5：停止约 -1.04mV，正反转中位数约 239~251mV。
    # 2026-10-19 A4：停止约 -1.15mV，鸣响约 16.5mV。
    # 2026-10-19 A2：停止约 -0.53mV，运行约 99.5~100.7mV。
    # 2026-10-19 A1 更换故障模块后：断电约 -0.63mV，通电约 274.8mV。
    feedback_voltage_limits: dict = field(default_factory=lambda: {
        "servo": (0.0100, 0.0115), "stepper": (0.010, 0.150),
        "buzzer": (0.005, 0.010),
        "fan": (0.010, 0.060),
        "lock": (0.010, 0.150),
    })


@dataclass(frozen=True)
class CurrentConfig:
    # 图片无法确认 ADS1115 与分流电阻，默认关闭；确认接线和量程后开启
    enabled: bool = False
    conversion_seconds: float = 0.010
    full_scale_volts: float = 2.048  # 必须与驱动中的 PGA 配置匹配
    channels: dict = field(default_factory=_current_channels)


def validate(self):
    timings = (self.current.conversion_seconds,)
    if any(not math.isfinite(value) or value <= 0 for value in timings):
        raise ValueError("Timing, frequency and scale values must be finite and positive")
    if self.ads1115.address not in range(0x48, 0x4c) or self.ads1115.full_scale_volts not in (6.144, 4.096, 2.048, 1.024, 0.512, 0.256):
        raise ValueError("ADS1115 地址或电压量程无效")
    if not math.isfinite(self.ads1115.timeout_seconds) or not 0 < self.ads1115.timeout_seconds <= 2:
        raise ValueError("ADS1115 超时必须大于 0 且不超过 2 秒")
    if any(name not in ("servo", "stepper", "buzzer", "rgb", "fan", "lock")
           or isinstance(channel, bool) or channel not in range(4)
           for name, channel in self.ads1115.feedback_channels.items()):
        raise ValueError("ADS1115 反馈设备或通道无效")
    if any(name not in self.ads1115.feedback_channels or address not in range(0x48, 0x4c)
           for name, address in self.ads1115.feedback_addresses.items()):
        raise ValueError("ADS1115 反馈地址无效")
    for name, limits in self.ads1115.feedback_voltage_limits.items():
        if (name not in self.ads1115.feedback_channels or len(limits) != 2
                or not all(math.isfinite(v) for v in limits)
                or not 0 <= limits[0] < limits[1] < self.ads1115.full_scale_volts):
            raise ValueError("ADS1115 反馈电压阈值无效")
    if self.current.full_scale_volts != 2.048:
        raise ValueError("ADS1115 当前驱动使用 ±2.048V PGA，full_scale_volts 必须匹配")
    locations = set()
    for name, channel in self.current.channels.items():
        if channel.channel not in range(4) or channel.shunt_ohms <= 0 or channel.threshold_ma < 0:
            raise ValueError(f"{name} 电流通道/分流电阻/阈值无效")
        location = (channel.address, channel.channel)
        if location in locations:
            raise ValueError("ADS1115 采样通道重复")
        locations.add(location)
        if not math.isfinite(channel.shunt_ohms) or not math.isfinite(channel.threshold_ma):
            raise ValueError(f"{name} 电流电阻/阈值必须有限")
        if channel.max_current_ma is not None and (not math.isfinite(channel.max_current_ma)
                                                  or channel.max_current_ma <= channel.threshold_ma):
            raise ValueError(f"{name} 运行电流上限必须大于运行下限")
        if channel.off_current_ma is not None and (not math.isfinite(channel.off_current_ma)
                                                  or not 0 < channel.off_current_ma <= channel.threshold_ma):
            raise ValueError(f"{name} 关闭电流上限必须为正且不大于运行下限")
