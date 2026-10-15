"""BH1750 单次高分辨率光照读取。"""
import time


class LightDriver:
    def __init__(self, i2c, config):
        self.i2c = i2c
        self.config = config

    def read(self):
        with self.i2c.lock:
            self.i2c.open()
            self.i2c.bus.write_byte(self.config.address, 0x01)
            self.i2c.bus.write_byte(self.config.address, 0x20)  # 单次高分辨率模式
            time.sleep(self.config.measurement_seconds)
            data = self.i2c.read_bytes(self.config.address, 2)
        return {"lux": round(((data[0] << 8) | data[1]) / self.config.counts_per_lux, 1)}
