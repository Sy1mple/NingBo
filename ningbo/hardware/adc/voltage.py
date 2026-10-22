"""ADS1115 单端四路电压采集，共享项目 I2C 锁，轮询 OS 位等待转换。"""
import time


class Ads1115Monitor:
    def __init__(self, i2c, config):
        self.i2c = i2c
        self.config = config

    def read_channel(self, channel, address=None, full_scale_volts=None):
        if not self.config.enabled:
            raise RuntimeError("ADS1115 电压采集未启用")
        address = self.config.address if address is None else address
        fsr = self.config.full_scale_volts if full_scale_volts is None else full_scale_volts
        ranges = (6.144, 4.096, 2.048, 1.024, 0.512, 0.256)
        if channel not in range(4) or address not in range(0x48, 0x4c) or fsr not in ranges:
            raise ValueError("ADS1115 通道、地址或量程无效")
        # 单次转换、128 SPS；禁用 RDY，用 OS 位确认完成，不读取浮空 GPIO。
        register = 0x8183 | ((4 + channel) << 12) | (ranges.index(fsr) << 9)
        with self.i2c.lock:
            self.i2c.open()
            bus = self.i2c.bus
            bus.write_i2c_block_data(address, 1, [register >> 8, register & 255])
            deadline = time.monotonic() + self.config.timeout_seconds
            configuration_retries = 0
            while True:
                state = bus.read_i2c_block_data(address, 1, 2)
                if len(state) != 2:
                    raise IOError("ADS1115 配置寄存器读取不完整")
                observed = (state[0] << 8) | state[1]
                if (observed & 0x7fff) != (register & 0x7fff):
                    # 瞬时配置丢失不能被当成当前通道数据；有限重写后重新等转换。
                    if configuration_retries < 2 and time.monotonic() < deadline:
                        configuration_retries += 1
                        bus.write_i2c_block_data(address, 1, [register >> 8, register & 255])
                        time.sleep(0.002)
                        continue
                    raise IOError(f"ADS1115 配置读回不一致: expected=0x{register:04X}, actual=0x{observed:04X}")
                if observed & 0x8000:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("ADS1115 转换超时")
                time.sleep(0.002)
            data = bus.read_i2c_block_data(address, 0, 2)
            if len(data) != 2:
                raise IOError("ADS1115 转换寄存器读取不完整")
        raw = int.from_bytes(bytes(data), "big", signed=True)
        return {"channel": channel, "raw": raw, "volts": round(raw * fsr / 32768, 6),
                "saturated": raw in (-32768, 32767), "sample_time": time.time()}

    def read_all(self):
        # 一轮中的通道也保持同一个锁，避免项目内其它 ADC 操作插入。
        with self.i2c.lock:
            readings = [self.read_channel(channel) for channel in range(4)]
        return {"bus": self.i2c.bus_number, "address": hex(self.config.address),
                "full_scale_volts": self.config.full_scale_volts, "readings": readings,
                "check_scope": "adc_voltage"}
