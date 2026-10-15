"""体温、距离与识别结果的 RGB 联动，独立于屏幕 VP 和 GPIO 驱动。"""
import math
import time


def valid_number(state, field, now, max_age, low, high):
    """无效、故障或过期读数不参与正常/异常判定。"""
    if state.get("status") in ("error", "disabled", "unconfigured", "unknown"):
        return None
    value = state.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    sampled = state.get("sample_time")
    if sampled is not None:
        if (isinstance(sampled, bool) or not isinstance(sampled, (int, float))
                or not math.isfinite(sampled) or not 0 <= now - sampled <= max_age):
            return None
    return value if low <= value <= high else None


class RgbFeedback:
    SOURCES = ("body_temperature", "ultrasonic", "access", "face_enroll")

    def __init__(self, devices, config, wall_clock=None):
        self.devices = devices
        self.config = config
        self.policy = config.rgb_feedback
        self.wall_clock = wall_clock or time.time
        self.results = {}
        self._last_color = None
        self._used = False

    def activate(self, source):
        if source in self.SOURCES:
            self.results[source] = self.policy.waiting_color
            self._used = True

    def release(self, source):
        self.results.pop(source, None)

    def observe(self, sensors):
        now = self.wall_clock()
        if "body_temperature" in self.results:
            low, high = self.policy.body_temperature_valid_range
            value = valid_number(sensors.get("body_temperature") or {}, "temperature", now,
                                 self.policy.sample_max_age_seconds, low, high)
            self.results["body_temperature"] = (self.policy.waiting_color if value is None else
                                                "red" if value > self.policy.body_temperature_high_c else "green")
        if "ultrasonic" in self.results:
            value = valid_number(sensors.get("ultrasonic") or {}, "distance", now,
                                 self.policy.sample_max_age_seconds, 0, self.config.ultrasonic.maximum_distance_cm)
            if value is None:
                self.results["ultrasonic"] = self.policy.waiting_color
            elif value < self.policy.distance_danger_cm:
                self.results["ultrasonic"] = "red"
            elif value < self.policy.distance_caution_cm:
                self.results["ultrasonic"] = self.policy.waiting_color
            else:
                self.results["ultrasonic"] = "green"

    def recognition(self, recognized):
        self.set_result("access", recognized)

    def set_result(self, source, recognized):
        if not isinstance(recognized, bool):
            raise ValueError("识别结果 recognized 必须是布尔值")
        if source in self.results:
            self.results[source] = "green" if recognized else "red"

    def color(self):
        colors = tuple(self.results.values())
        if "red" in colors:
            return "red"
        if self.policy.waiting_color in colors:
            return self.policy.waiting_color
        return "green" if "green" in colors else "off"

    def flush(self, snapshot=None):
        if not self.policy.enabled or not self.config.rgb.enabled or not self._used:
            return None
        desired = self.color()
        actual = ((snapshot or {}).get("actuators", {}).get("rgb") or {}).get("color")
        if desired == self._last_color and (actual is None or actual == desired):
            if not self.results:
                self._used = False
            return None
        # 写成功后才缓存颜色，失败时下次循环继续尝试。
        result = self.devices.control("rgb", color=desired)
        self._last_color = desired
        if not self.results:
            self._used = False
        return result

    def reset(self):
        self.results.clear()
        self._last_color = None
        self._used = False
