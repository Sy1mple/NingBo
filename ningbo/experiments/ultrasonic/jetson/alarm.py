"""测距蜂鸣器联动：有效距离小于危险阈值时鸣响，与 RGB 颜色优先级独立。"""
import logging
import time

from .readings import valid_number

LOGGER = logging.getLogger(__name__)


class ProximityAlarm:
    def __init__(self, devices, config, wall_clock=None):
        self.devices = devices
        self.config = config
        self.wall_clock = wall_clock or time.time
        self.reset()

    def reset(self):
        self.active = False
        self.desired = False
        self._used = False
        self._last_on = None

    def activate(self):
        self.active = True
        self.desired = False
        self._used = True

    def release(self):
        self.active = False
        self.desired = False

    def observe(self, sensors):
        policy = self.config.rgb_feedback
        distance = valid_number(
            sensors.get("ultrasonic") or {}, "distance", self.wall_clock(),
            policy.sample_max_age_seconds, 0, self.config.ultrasonic.maximum_distance_cm,
        )
        self.desired = (self.active and self.config.ultrasonic.near_buzzer_enabled
                        and distance is not None and distance < policy.distance_danger_cm)

    def flush(self, snapshot=None):
        if not self.config.buzzer.enabled or not self._used:
            return None
        actual = ((snapshot or {}).get("actuators", {}).get("buzzer") or {}).get("on")
        if self.desired == self._last_on and (actual is None or actual == self.desired):
            if not self.active:
                self._used = False
            return None
        # 输出成功后才记录，关闭失败时后续主循环仍会重试。
        result = self.devices.control("buzzer", on=self.desired)
        self._last_on = self.desired
        LOGGER.info("测距蜂鸣器：%s", "开启（近距离）" if self.desired else "关闭")
        if not self.active:
            self._used = False
        return result
