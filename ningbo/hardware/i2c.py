"""The one I2C connection and its bus lock; no scene logic."""
import threading

class I2cResource:
    def __init__(self, bus_number, factory=None, message_factory=None):
        self.bus_number = bus_number
        self.factory = factory
        self.message_factory = message_factory
        self.bus = None
        self.lock = threading.RLock()

    def open(self):
        if self.bus is None:
            if self.factory is None:
                from smbus2 import SMBus
                self.factory = SMBus
            self.bus = self.factory(self.bus_number)

    def close(self):
        with self.lock:
            if self.bus is not None:
                self.bus.close()
                self.bus = None

    def read_bytes(self, address, count):
        """无寄存器地址的原生 I2C 读，用于 BH1750 等流式设备。"""
        with self.lock:
            self.open()
            if self.message_factory is None:
                from smbus2 import i2c_msg
                self.message_factory = i2c_msg
            message = self.message_factory.read(address, count)
            self.bus.i2c_rdwr(message)
            return list(message)

    def read_register_bytes(self, address, register, count):
        """寄存器地址写入与 repeated START 读取共用总线锁。"""
        with self.lock:
            self.open()
            if self.message_factory is None:
                from smbus2 import i2c_msg
                self.message_factory = i2c_msg
            command = self.message_factory.write(address, [register])
            response = self.message_factory.read(address, count)
            self.bus.i2c_rdwr(command, response)
            return list(response)
