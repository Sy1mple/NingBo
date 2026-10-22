"""人体温度场景的非阻塞短鸣；仅在开始采集且有效温度超过阈值时报警。"""
import time

from .readings import valid_number


class BodyTemperatureAlarm:
    def __init__(self, devices, config, clock=None, wall_clock=None):
        self.devices = devices
        self.config = config
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.reset()

    def reset(self):
        self.active = False
        self.desired = False
        self._started = None
        self._used = False
        self._last_on = None

    def activate(self):
        self.active = True
        self.desired = False
        self._started = None
        self._used = True

    def release(self):
        self.active = False
        self.desired = False
        self._started = None

    def observe(self, sensors):
        policy = self.config.rgb_feedback
        low, high = policy.body_temperature_valid_range
        value = valid_number(sensors.get("body_temperature") or {}, "temperature",
                             self.wall_clock(), policy.sample_max_age_seconds, low, high)
        if not (self.active and value is not None and value > policy.body_temperature_high_c):
            self._started = None
            self.desired = False
            return
        now = self.clock()
        if self._started is None:
            self._started = now
        elapsed = (now - self._started) % self.config.body_temperature.alarm_period_seconds
        self.desired = elapsed < self.config.body_temperature.alarm_on_seconds

    def flush(self, snapshot=None):
        if not self._used or not self.config.buzzer.enabled:
            return None
        if self.desired == self._last_on:
            if not self.active:
                self._used = False
            return None
        result = self.devices.control("buzzer", on=self.desired)
        self._last_on = self.desired
        if not self.active:
            self._used = False
        return result
