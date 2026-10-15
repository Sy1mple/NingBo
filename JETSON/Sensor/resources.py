"""共享硬件资源：延迟加载平台依赖，每个服务只持有一套资源。"""
import threading
from pathlib import Path


class GpioResource:
    def __init__(self, config, module=None):
        self.config = config
        self.module = module
        self._owned = set()
        self._opened = False
        self.lock = threading.RLock()

    def open(self):
        if self._opened:
            return
        if self.module is None:
            import Jetson.GPIO as GPIO
            self.module = GPIO
        self.module.setmode(self.module.BOARD)
        self.module.setwarnings(False)
        self._opened = True

    def setup_output(self, pin, level=0):
        with self.lock:
            self.module.setup(pin, self.module.OUT, initial=self.module.HIGH if level else self.module.LOW)
            self._owned.add(pin)

    def setup_input(self, pin, pull_up=False):
        with self.lock:
            kwargs = {"pull_up_down": self.module.PUD_UP} if pull_up else {}
            self.module.setup(pin, self.module.IN, **kwargs)
            self._owned.add(pin)

    def write(self, pin, level):
        self.module.output(pin, self.module.HIGH if level else self.module.LOW)

    def read(self, pin):
        return int(self.module.input(pin))

    def create_pwm(self, pin, frequency_hz):
        """由 Jetson.GPIO 分配引脚对应的硬件 PWM，不用软件模拟。"""
        with self.lock:
            self.open()
            pwm = self.module.PWM(pin, frequency_hz)
            self._owned.add(pin)
            return pwm

    def pwm_state(self, pwm):
        """读取内核 PWM 状态；不能证明引脚波形或舵机实际角度。"""
        info = pwm._ch_info
        path = Path(info.pwm_chip_dir) / f"pwm{info.pwm_id}"
        return {name: int((path / name).read_text().strip())
                for name in ("period", "duty_cycle", "enable")}

    def release_pwm(self, pin):
        with self.lock:
            self.module.cleanup(pin)
            self._owned.discard(pin)

    def close(self):
        with self.lock:
            if self._opened and self._owned:
                self.module.cleanup(tuple(self._owned))
            self._owned.clear()
            self._opened = False


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
