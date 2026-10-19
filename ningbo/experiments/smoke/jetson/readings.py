"""smoke: interpret real device snapshots without inventing sensor data."""
import logging
import time
import math

LOGGER = logging.getLogger(__name__)

class SnapshotReader:
    def __init__(self, config, wall_clock=None, light_full_scale_lux=None):
        self.config = config
        self.wall_clock = wall_clock or time.time
        self.light_full_scale_lux = light_full_scale_lux or config.light.display_full_scale_lux
        self._smoke_display_overflow = False

    def values(self, snapshot):
        sensors = snapshot.get("sensors", {})
        smoke = sensors.get("smoke") or {}
        if smoke.get("protocol") == "ffaa":
            value = valid_number(smoke, "concentration", self.wall_clock(),
                                 self.config.smoke.stale_after_seconds, 0, 0xFFFF)
            if value is None:
                return {}
            if value > 0x7FFF:
                if not self._smoke_display_overflow:
                    LOGGER.warning("烟雾浓度超过当前屏幕有符号 Word 显示范围，未截断数值")
                self._smoke_display_overflow = True
                return {}
            self._smoke_display_overflow = False
            return {"value": value}
        # Actual UART payload is only raw bytes. Only send a percent after a
        # separate sensor-protocol parser + calibration provides it.
        percent = smoke.get("percent")
        return {"value": max(0, min(100, round(percent)))} if percent is not None else {}


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
