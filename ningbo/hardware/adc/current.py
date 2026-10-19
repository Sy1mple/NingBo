"""ADS1115 分流电阻电流读取；诊断只在已启用的真实通道上执行。"""
from .config import Ads1115Config
from hardware.adc.voltage import Ads1115Monitor


class CurrentMonitor:
    def __init__(self, i2c, config):
        self.i2c = i2c
        self.config = config
        self.adc = Ads1115Monitor(i2c, Ads1115Config(enabled=config.enabled,
                                                  full_scale_volts=config.full_scale_volts))

    def read_ma(self, name):
        if not self.config.enabled:
            raise RuntimeError("电流检测未启用，请先确认 ADS1115 与分流电阻配置")
        channel = self.config.channels[name]
        sample = self.adc.read_channel(channel.channel, address=channel.address)
        if sample['saturated']:
            raise ValueError("ADS1115 电流反馈超出量程")
        volts = sample['raw'] * self.config.full_scale_volts / 32768
        return round(volts / channel.shunt_ohms * 1000, 1)
