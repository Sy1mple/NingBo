"""Servo screen/start/stop, sweep command and target-angle display."""
import math


class ServoScreenBridge:
    def __init__(self, devices, config, screen=None):
        self.devices = devices
        self.config = config
        self.screen = screen
        self._servo_display_active = False
        self._servo_next_display = 0.0
        self._servo_last_angle = None

    def handle_event(self, event):
        name = event["event"] if isinstance(event, dict) else event
        if name == "servo.start":
            if self._servo_display_active:
                return {"event": name, "handled": True, "sent": [], "note": "舵机已在往返运行"}
            result = self.devices.control("servo", sweep=True)
            angle = result.get("angle", self.config.servo.sweep_min_angle)
            try:
                previous = self._servo_last_angle
                if previous is None:
                    previous = self.config.servo.default_angle
                sent = self.screen.handle_message(self._servo_message(angle, previous)) if self.screen else []
            except Exception:
                self.devices.stop_actuator("servo")
                raise
            self._servo_display_active = True
            self._servo_last_angle = angle
            self._servo_next_display = 0.0
            return {"event": name, "handled": True, "sent": sent, "result": result,
                    "note": f"舵机循环启动：{self.config.servo.sweep_min_angle:g}°→{self.config.servo.sweep_max_angle:g}°→{self.config.servo.default_angle:g}°，每个位置停留{self.config.servo.sweep_interval_seconds:g}秒"}
        if name == "servo.stop":
            self._servo_display_active = False
            result = self.devices.stop_actuator("servo")
            sent = []
            if self.screen:
                sent = self.screen.handle_message({"scene": "servo", "direction": "停止", "target_angle": 0})
            return {"event": name, "handled": True, "sent": sent, "result": result}
        return None

    def reset(self):
        self._servo_display_active = False
        self._servo_next_display = 0.0
        self._servo_last_angle = None

    def _valid_servo_angle(self, angle):
        return (not isinstance(angle, bool) and isinstance(angle, (int, float)) and math.isfinite(angle)
                and self.config.servo.min_angle <= angle <= self.config.servo.max_angle)

    def _servo_message(self, angle, previous):
        # 按目标角度增减显示指令方向；舵机没有实际方向反馈。
        direction = "反转" if angle >= previous else "正转"
        return {"scene": "servo", "direction": direction, "target_angle": angle}

    def tick(self, snapshot, now):
        if not self._servo_display_active or self.screen is None or now < self._servo_next_display:
            return []
        state = snapshot.get("actuators", {}).get("servo")
        if not state:
            return []
        if not state.get("on") or state.get("driver_error"):
            self._servo_display_active = False
            return []
        angle = state.get("angle")
        if not self._valid_servo_angle(angle) or angle == self._servo_last_angle:
            return []
        sent = self.screen.handle_message(self._servo_message(angle, self._servo_last_angle))
        self._servo_last_angle = angle
        self._servo_next_display = now + self.config.servo.display_interval_seconds
        return sent
