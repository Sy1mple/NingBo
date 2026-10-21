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
        self._next_log = 0

    def values(self, snapshot):
        sensors = snapshot.get("sensors", {})
        smoke = sensors.get("smoke") or {}
        value = display_percent(smoke, self.config, self.wall_clock())
        if value is not None and time.monotonic() >= self._next_log:
            LOGGER.info('烟雾演示换算：raw=%s；参考=%d；满量程=%d；显示=%d',
                        smoke.get('concentration', smoke.get('percent')),
                        self.config.smoke.display_baseline_raw,
                        self.config.smoke.display_full_scale_raw, value)
            self._next_log = time.monotonic() + 5
        return {"value": value} if value is not None else {}


def display_percent(state, config, now):
    """固定正常空气参考的演示指数；写屏、RGB 和蜂鸣器共用。"""
    if state.get("protocol") == "ffaa":
        raw = valid_number(state, "concentration", now,
                           config.smoke.stale_after_seconds, 0, 65535)
        if raw is None:
            return None
        cfg = config.smoke
        value = cfg.display_normal_value + (raw - cfg.display_baseline_raw) * (
            100 - cfg.display_normal_value) / (cfg.display_full_scale_raw - cfg.display_baseline_raw)
    else:
        value = valid_number(state, "percent", now,
                             config.smoke.stale_after_seconds, 0, 100)
        if value is None:
            return None
    return max(0, min(100, int(value + 0.5)))



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
