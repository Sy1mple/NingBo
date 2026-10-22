"""GY-906 / MLX90614 只读驱动，使用共享 I2C 总线和严格 SMBus PEC 校验。

0x06 为环境温度，0x07 为目标表面温度；不写 EEPROM 或改变发射率。
协议：https://www.melexis.com/en/documents/documentation/datasheets/datasheet-mlx90614
"""


def smbus_pec(data):
    """CRC-8，初始值 0，多项式 x^8+x^2+x+1 (0x07)。"""
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 0xFF
    return crc


class Mlx90614Driver:
    AMBIENT_REGISTER = 0x06
    OBJECT_REGISTER = 0x07

    def __init__(self, i2c, config):
        self.i2c = i2c
        self.config = config

    def _read_temperature(self, register, limits):
        address = self.config.address
        data = self.i2c.read_register_bytes(address, register, 3)
        if len(data) != 3:
            raise OSError("MLX90614 温度响应长度错误")
        low, high, received_pec = data
        expected = smbus_pec((address << 1, register, (address << 1) | 1, low, high))
        if received_pec != expected:
            raise ValueError(f"MLX90614 PEC 校验失败，寄存器 0x{register:02X}")
        raw = low | high << 8
        if raw & 0x8000:
            raise ValueError(f"MLX90614 温度错误标志，寄存器 0x{register:02X}")
        temperature = raw * 0.02 - 273.15
        if not limits[0] <= temperature <= limits[1]:
            raise ValueError(f"MLX90614 温度超出量程，寄存器 0x{register:02X}")
        return round(temperature, 2)

    def read(self):
        # 两个寄存器使用同一个共享 RLock，避免与其他 I2C 驱动交错。
        if not self.i2c.lock.acquire(timeout=0.5):
            raise TimeoutError("MLX90614 等待共享 I2C 总线超过 0.5 秒")
        try:
            ambient = self._read_temperature(self.AMBIENT_REGISTER, self.config.ambient_range)
            target = self._read_temperature(self.OBJECT_REGISTER, self.config.temperature_range)
        finally:
            self.i2c.lock.release()
        return {"temperature": target, "ambient_temperature": ambient,
                "model": "mlx90614", "address": self.config.address}
