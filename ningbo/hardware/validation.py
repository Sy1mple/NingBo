"""Cross-device connection validation only."""

import math


def validate(self):
    timings = (self.thread_join_timeout,)
    if any(not math.isfinite(value) or value <= 0 for value in timings):
        raise ValueError("Timing, frequency and scale values must be finite and positive")
    if self.gpio_numbering != "BOARD":
        raise ValueError("当前接线配置只支持 BOARD 物理编号")
    pins = [pin for pin, _ in self.gpio_assignments()]
    allowed = {7, 11, 12, 13, 15, 16, 18, 19, 21, 22, 23, 24, 26, 29, 31, 32, 33, 35, 36, 37, 38, 40}
    if len(pins) != len(set(pins)):
        raise ValueError("config.py 中存在 GPIO 引脚冲突")
    if set(pins) - allowed or set(pins) & (set(self.i2c_pins) | set(self.uart_pins)):
        raise ValueError("GPIO 配置占用了电源/地/总线或无效引脚")
    if self.smoke.enabled and self.screen.port == self.smoke.port:
        raise ValueError("串口屏和烟雾传感器不能共用同一个串口")
    if self.i2c_bus < 0:
        raise ValueError("I2C 总线号必须非负")
    addresses = [device.address for device in (self.light,) if device.enabled]
    if self.current.enabled:
        addresses += sorted({ch.address for ch in self.current.channels.values()})
    if len(addresses) != len(set(addresses)) or any(not 0x08 <= addr <= 0x77 for addr in addresses):
        raise ValueError("I2C 地址冲突或不在 7 位设备地址范围")
    if self.ads1115.enabled and self.ads1115.address in {d.address for d in (self.light,) if d.enabled}:
        raise ValueError("ADS1115 与其他 I2C 设备地址冲突")
