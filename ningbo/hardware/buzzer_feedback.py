"""合并测温短鸣与原有蜂鸣输出；原有场景之间沿用最后一次控制的语义。"""


class BuzzerFeedback:
    def __init__(self, devices):
        self.devices = devices
        self.requests = {}
        self._last_on = None
        self._used = False

    def source(self, name):
        return _BuzzerSource(self, name)

    def request(self, source, on):
        if not isinstance(on, bool):
            raise ValueError("蜂鸣请求 on 必须为布尔值")
        # 原有测距/烟雾/门禁仍按原先的控制顺序共享输出。
        # 单独保留新增测温请求，让其脉冲间歇不关闭原有场景的报警。
        group = "body_temperature" if source == "body_temperature" else "existing_scenes"
        if on:
            self.requests[group] = True
        else:
            self.requests.pop(group, None)
        self._used = True
        return self.flush()

    def flush(self):
        if not self._used:
            return None
        desired = any(self.requests.values())
        actual = (self.devices.snapshot().get("actuators", {}).get("buzzer") or {}).get("on")
        if desired == self._last_on and (actual is None or actual == desired):
            if not self.requests:
                self._used = False
            return None
        result = self.devices.control("buzzer", on=desired)
        self._last_on = desired
        if not self.requests:
            self._used = False
        return result

    def reset(self):
        if self._used or self.requests:
            # 关闭成功后再清理，失败时保留请求供下一次重试。
            self.devices.control("buzzer", on=False)
        self.requests.clear()
        self._last_on = None
        self._used = False


class _BuzzerSource:
    def __init__(self, feedback, name):
        self.feedback = feedback
        self.name = name

    def control(self, name, **kwargs):
        if name == "buzzer":
            if set(kwargs) != {"on"}:
                raise ValueError("蜂鸣器场景请求只支持 on")
            return self.feedback.request(self.name, kwargs["on"])
        return self.feedback.devices.control(name, **kwargs)

    def __getattr__(self, name):
        return getattr(self.feedback.devices, name)
