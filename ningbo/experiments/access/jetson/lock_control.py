"""门禁继电器联动。只在明确启用且收到确认识别结果后限时通电。"""
import time


class AccessLockControl:
    def __init__(self, devices, config, clock=None):
        self.devices = devices
        self.config = config
        self.clock = clock or time.monotonic
        self._deadline = None
        self._closing = False

    @property
    def active(self):
        return self._deadline is not None

    def result(self, recognized):
        if not self.config.unlock_on_match:
            return
        if recognized is True:
            if self._deadline is None:
                self._deadline = self.clock() + self.config.unlock_seconds
                try:
                    self.devices.control("lock", on=True)
                except Exception:
                    self._closing = True
                    raise
        else:
            self.close()

    def tick(self):
        if self._deadline is not None and (self._closing or self.clock() >= self._deadline):
            self.close()

    def close(self):
        if self._deadline is not None:
            self._closing = True
            # 输出失败时保留关闭请求，下次主循环继续重试。
            self.devices.control("lock", on=False)
            self._deadline = None
            self._closing = False


class AccessFailureAlarm:
    """智能门禁验证失败时直接鸣响，到期关闭；计时由门禁场景管理。"""
    def __init__(self, output, seconds, clock=None):
        self.output, self.seconds = output, seconds
        self.clock = clock or time.monotonic
        self._deadline = None

    @property
    def active(self):
        return self._deadline is not None

    def result(self, recognized):
        if recognized is False and self._deadline is None:
            self._deadline = self.clock() + self.seconds
            self.output(True)
        elif recognized is True:
            self.close()

    def tick(self):
        if self._deadline is not None and self.clock() >= self._deadline:
            self.close()

    def close(self):
        if self._deadline is not None:
            self.output(False)
            self._deadline = None
