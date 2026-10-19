"""体温、距离与识别结果的 RGB 联动，独立于屏幕 VP 和 GPIO 驱动。"""
import time
from experiments.body_temperature.jetson.feedback import color_for as body_color
from experiments.ultrasonic.jetson.feedback import color_for as distance_color

SENSOR_COLOR_RULES = {"body_temperature": body_color, "ultrasonic": distance_color}




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
        for source, evaluate in SENSOR_COLOR_RULES.items():
            if source in self.results:
                self.results[source] = evaluate(sensors.get(source) or {}, self.config, now)

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
