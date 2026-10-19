"""步进启停与运行状态写屏；沿用舵机的立即写入和主循环刷新方式。"""


class StepperScreenBridge:
    PARAMETER_NAMES = ("stepper.direction", "stepper.steps")

    def __init__(self, devices, config, screen=None):
        self.devices = devices
        self.config = config
        self.screen = screen
        self.parameters = {}
        self._display_active = False
        self._last_display = None
        self._next_display = 0.0

    def reset(self):
        self.parameters.clear()
        self._display_active = False
        self._last_display = None
        self._next_display = 0.0

    def _message(self, state):
        directions = {'fwd': '正转', 'rev': '反转', 'stop': '停止'}
        if state.get('dir') not in directions:
            raise ValueError('步进快照缺少有效方向，不能生成显示')
        return {'scene': 'stepper', 'direction': directions[state['dir']], 'steps': state['n']}

    def _send_state(self, state):
        if self.screen is None:
            return []
        message = self._message(state)
        values = (message['direction'], message['steps'])
        if values == self._last_display:
            return []
        sent = self.screen.handle_message(message)
        self._last_display = values
        return sent

    def tick(self, snapshot, now):
        if not self._display_active or self.screen is None or now < self._next_display:
            return []
        state = snapshot.get('actuators', {}).get('stepper')
        if not state:
            return []
        sent = self._send_state(state)
        self._next_display = now + self.config.stepper.display_interval_seconds
        if state.get('dir') == 'stop':
            self._display_active = False
        return sent

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
                    raise ValueError("屏幕方向值与 config.py 中的约定不一致")
            if name == "stepper.steps" and not 1 <= value <= self.config.stepper.maximum_steps:
                raise ValueError("步进步数必须为正且在配置范围内")
            self.parameters[name] = value
            return {"event": name, "handled": True, "sent": [], "note": "控制参数已接收"}
        if name == "stepper.start":
            if self._display_active:
                state = self.devices.snapshot().get('actuators', {}).get('stepper', {})
                if state.get('running'):
                    return {'event': name, 'handled': True, 'sent': [], 'note': '步进电机已在运行'}
            # Non-editable screen controls may never send their initial values.
            direction_value = self.parameters.get("stepper.direction", self.config.screen_control.forward_value)
            steps = self.parameters.get("stepper.steps", min(512, self.config.stepper.maximum_steps))
            direction = "fwd" if direction_value == self.config.screen_control.forward_value else "rev"
            result = self.devices.control("stepper", dir_=direction, n=steps,
                                          spd=self.config.stepper.default_speed_ms)
            try:
                self._last_display = None
                sent = self._send_state(result)
            except Exception:
                # 与舵机一致，初次写屏失败时停止电机，保留停止状态的刷新。
                self._display_active = True
                self._next_display = 0.0
                self.devices.stop_actuator('stepper')
                raise
            self._display_active = True
            self._next_display = 0.0
            return {"event": name, "handled": True, "sent": sent, "result": result,
                    "note": f"步进启动：{direction}，{steps}步，间隔{self.config.stepper.default_speed_ms}ms"}
        if name == "stepper.stop":
            result = self.devices.stop_actuator("stepper")
            # 电机先停止；写屏失败时保留刷新请求，在下一次 tick 重试。
            self._display_active = True
            self._next_display = 0.0
            sent = self._send_state(result)
            self._display_active = False
            return {"event": name, "handled": True, "sent": sent, "result": result}
        return None
