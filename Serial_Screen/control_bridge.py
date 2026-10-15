"""串口屏启停按钮和控制参数到执行器 API 的转换。"""
import math


class ControlScreenBridge:
    PARAMETER_NAMES = ("stepper.direction", "stepper.steps")

    def __init__(self, devices, config, screen=None):
        self.devices = devices
        self.config = config
        self.screen = screen
        self.parameters = {}
        self._servo_display_active = False
        self._servo_next_display = 0.0
        self._servo_last_angle = None

    def handle_event(self, event):
        name = event["event"] if isinstance(event, dict) else event
        if name in self.PARAMETER_NAMES:
            value = event.get("value") if isinstance(event, dict) else None
            self.parameters.pop(name, None)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
                raise ValueError(f"{name} 必须是 16 位 Word")
            if name.endswith("direction"):
                cfg = self.config.screen_control
                if value not in (cfg.forward_value, cfg.reverse_value):
                    raise ValueError("屏幕方向值与 Config.py 中的约定不一致")
            if name == "stepper.steps" and not 1 <= value <= self.config.stepper.maximum_steps:
                raise ValueError("步进步数必须为正且在配置范围内")
            self.parameters[name] = value
            return {"event": name, "handled": True, "sent": [], "note": "控制参数已接收"}
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
        if name == "stepper.start":
            # Non-editable screen controls may never send their initial values.
            direction_value = self.parameters.get("stepper.direction", self.config.screen_control.forward_value)
            steps = self.parameters.get("stepper.steps", min(512, self.config.stepper.maximum_steps))
            direction = "fwd" if direction_value == self.config.screen_control.forward_value else "rev"
            result = self.devices.control("stepper", dir_=direction, n=steps,
                                          spd=self.config.stepper.default_speed_ms)
            return {"event": name, "handled": True, "sent": [], "result": result,
                    "note": f"步进启动：{direction}，{steps}步，间隔{self.config.stepper.default_speed_ms}ms"}
        if name in ("servo.stop", "stepper.stop"):
            if name == "servo.stop":
                self._servo_display_active = False
            result = self.devices.stop_actuator(name.split(".")[0])
            sent = []
            if name == "servo.stop" and self.screen:
                sent = self.screen.handle_message({"scene": "servo", "direction": "停止", "target_angle": 0})
            return {"event": name, "handled": True, "sent": sent, "result": result}
        if name in ("fan.start", "fan.stop"):
            result = self.devices.control("fan", on=(name == "fan.start"))
            return {"event": name, "handled": True, "sent": [], "result": result}
        return None

    def reset(self):
        self.parameters.clear()
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
