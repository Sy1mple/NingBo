"""继电器、蜂鸣器与 RGB 数字输出，支持配置高/低有效。"""
import threading


class SwitchDriver:
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


class RgbDriver:
    COLORS = {
        "off": (0, 0, 0), "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1),
        "yellow": (1, 1, 0), "cyan": (0, 1, 1), "magenta": (1, 0, 1), "white": (1, 1, 1),
    }

    def __init__(self, gpio, config):
        self.gpio = gpio
        self.config = config
        self.color = "off"
        self.lock = threading.RLock()

    def _level(self, value):
        return int(bool(value) == self.config.active_high)

    def start(self):
        for pin in self.config.pins:
            self.gpio.setup_output(pin, self._level(0))

    def set(self, color=None, r=None, g=None, b=None, off=False):
        if off:
            color = "off"
        elif color is None:
            flags = (int(bool(r)), int(bool(g)), int(bool(b)))
            color = next(name for name, pattern in self.COLORS.items() if pattern == flags)
        else:
            color = str(color).lower()
            color = {"r": "red", "g": "green", "b": "blue", "none": "off", "0": "off"}.get(color, color)
        if color not in self.COLORS:
            raise ValueError(f"未知 RGB 颜色: {color}")
        with self.lock:
            try:
                for pin, value in zip(self.config.pins, self.COLORS[color]):
                    self.gpio.write(pin, self._level(value))
            except Exception:
                # 部分输出失败时尽量关掉全部通道，不记录成功状态。
                for pin in self.config.pins:
                    try:
                        self.gpio.write(pin, self._level(0))
                    except Exception:
                        pass
                self.color = "off"
                raise
            self.color = color
            return self.snapshot()

    def stop(self):
        return self.set(off=True)

    def snapshot(self):
        with self.lock:
            return {"color": self.color, "on": self.color != "off"}
