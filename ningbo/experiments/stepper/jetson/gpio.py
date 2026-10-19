"""步进电机独立调度线程，正反转改变相序索引。"""
import threading


class StepperDriver:
    def __init__(self, gpio, config, join_timeout=5.0):
        self.gpio = gpio
        self.config = config
        self.join_timeout = join_timeout
        self.lock = threading.RLock()
        self._closing = threading.Event()
        self._thread = None
        self._index = 0
        self.remaining = 0
        self.direction = "stop"
        self.speed = config.default_speed_ms
        self.error = ""
        self._prepared_pins = set()

    def _write(self, pattern):
        for pin, value in zip(self.config.pins, pattern):
            self.gpio.write(pin, value)

    def start(self):
        if len(self._prepared_pins) != len(self.config.pins):
            self.prepare_idle()
        self._closing.clear()
        self._thread = threading.Thread(target=self._run, name="sensor-stepper", daemon=True)
        self._thread.start()

    def prepare_idle(self):
        """只接管相位脚并关闭线圈，不启动线程或输出运行相序。"""
        with self.lock:
            for pin in self.config.pins:
                self.gpio.setup_output(pin, 0)
                self._prepared_pins.add(pin)

    def _write_off(self):
        for pin in self.config.pins:
            if pin in self._prepared_pins:
                self.gpio.write(pin, 0)

    def set(self, dir_="stop", n=0, spd=None):
        if dir_ not in ("fwd", "rev", "stop"):
            raise ValueError("步进方向必须是 fwd、rev 或 stop")
        with self.lock:
            self.remaining = max(0, min(self.config.maximum_steps, int(n)))
            self.speed = max(self.config.minimum_speed_ms, min(self.config.maximum_speed_ms, int(spd if spd is not None else self.config.default_speed_ms)))
            self.direction = dir_ if self.remaining else "stop"
            self.error = ""
            if self.direction == "stop":
                self.remaining = 0
                self._write_off()
            return self.snapshot()

    def stop(self):
        return self.set(dir_="stop")

    def close(self):
        self._closing.set()
        try:
            self.stop()
        finally:
            if self._thread:
                self._thread.join(self.join_timeout)
                if self._thread.is_alive():
                    raise RuntimeError("步进线程未结束")
                self._thread = None
            self._prepared_pins.clear()

    def step_once(self):
        """一次相序推进，供线程调度和无硬件单元测试使用。"""
        with self.lock:
            if self.direction == "stop" or not self.remaining:
                return
            self._write(self.config.sequence[self._index])
            delta = 1 if self.direction == "fwd" else -1
            self._index = (self._index + delta) % len(self.config.sequence)
            self.remaining -= 1
            if not self.remaining:
                self.direction = "stop"
                self._write_off()

    def _run(self):
        while not self._closing.is_set():
            with self.lock:
                delay = self.speed / 1000 if self.direction != "stop" else self.config.idle_wait
                try:
                    self.step_once()
                except Exception as exc:
                    self.error = str(exc)
                    self.direction = "stop"
                    self.remaining = 0
                    try:
                        self._write_off()
                    except Exception:
                        pass
            self._closing.wait(delay)

    def snapshot(self):
        with self.lock:
            return {"dir": self.direction, "n": self.remaining, "spd": self.speed,
                    "running": self.direction != "stop", "note": self.error,
                    "pins_prepared": len(self._prepared_pins) == len(self.config.pins),
                    "driver_error": self.error, "worker_alive": bool(self._thread and self._thread.is_alive()),
                    "status": "error" if self.error else None}
