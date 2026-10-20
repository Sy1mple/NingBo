"""烟雾场景的阈值、RGB 颜色和非阻塞周期短鸣。"""
import time

from .readings import display_percent


def color_for(state, config, now):
    value = display_percent(state, config, now)
    if value is None:
        return config.rgb_feedback.waiting_color
    return "red" if value >= config.smoke.alarm_threshold else "green"


class SmokeAlarm:
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
        value = display_percent(sensors.get("smoke") or {}, self.config, self.wall_clock())
        high = self.active and value is not None and value >= self.config.smoke.alarm_threshold
        if not high:
            self._started = None
            self.desired = False
            return
        now = self.clock()
        if self._started is None:
            self._started = now
        elapsed = (now - self._started) % self.config.smoke.alarm_period_seconds
        self.desired = elapsed < self.config.smoke.alarm_on_seconds

    def flush(self, snapshot=None):
        if not self._used or not self.config.buzzer.enabled:
            return None
        # 仅输出变化；避免烟雾正常时持续重写共享蜂鸣器。
        if self.desired == self._last_on:
            if not self.active:
                self._used = False
            return None
        result = self.devices.control("buzzer", on=self.desired)
        self._last_on = self.desired
        if not self.active:
            self._used = False
        return result
