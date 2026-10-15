"""超声波 TRIG/ECHO 读取，返回厘米。"""
import threading
import time


class UltrasonicDriver:
    def __init__(self, gpio, config):
        self.gpio = gpio
        self.config = config
        self.lock = threading.Lock()

    def start(self):
        self.gpio.setup_output(self.config.trig_pin, 0)
        self.gpio.setup_input(self.config.echo_pin)

    def read(self):
        with self.lock:
            cfg = self.config
            self.gpio.write(cfg.trig_pin, 0)
            time.sleep(cfg.settle_seconds)
            self.gpio.write(cfg.trig_pin, 1)
            time.sleep(cfg.trigger_seconds)
            self.gpio.write(cfg.trig_pin, 0)
            until = time.perf_counter() + cfg.rise_timeout_seconds
            while not self.gpio.read(cfg.echo_pin):
                if time.perf_counter() > until:
                    raise TimeoutError("超声波 ECHO 未上升")
            start = time.perf_counter()
            while self.gpio.read(cfg.echo_pin):
                if time.perf_counter() - start > cfg.fall_timeout_seconds:
                    raise TimeoutError("超声波 ECHO 未下降")
            duration = time.perf_counter() - start
            distance = duration * cfg.sound_speed_cm_per_second / 2
            if duration * 1e6 < cfg.minimum_pulse_us or distance > cfg.maximum_distance_cm:
                raise ValueError("超声波脉宽或距离超出配置范围")
            return {"distance": round(distance, 1)}
