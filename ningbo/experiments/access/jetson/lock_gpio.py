"""access: complete GPIO initialization, levels, on/off and state."""
import threading

class LockDriver:
    def __init__(self, gpio, config):
        self.gpio = gpio
        self.config = config
        self.on = False
        self.lock = threading.RLock()

    def _level(self, on):
        return int(bool(on) == self.config.active_high)

    def start(self):
        self.gpio.setup_output(self.config.pin, self._level(False))

    def set(self, on):
        with self.lock:
            # 写成功后才更新状态，异常向调用方传播。
            self.gpio.write(self.config.pin, self._level(on))
            self.on = bool(on)
            return {"on": self.on}

    def stop(self):
        return self.set(False)

    def snapshot(self):
        with self.lock:
            return {"on": self.on}
