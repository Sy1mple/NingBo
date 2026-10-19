"""body_temperature: interpret real device snapshots without inventing sensor data."""
import logging
import time
import math

LOGGER = logging.getLogger(__name__)

class SnapshotReader:
    def __init__(self, config, wall_clock=None, light_full_scale_lux=None):
        self.config = config
        self.wall_clock = wall_clock or time.time
        self.light_full_scale_lux = light_full_scale_lux or config.light.display_full_scale_lux

    def values(self, snapshot):
        sensors = snapshot.get("sensors", {})
        low, high = self.config.body_temperature.display_range
        temperature = valid_number(sensors.get("body_temperature") or {}, "temperature", self.wall_clock(),
                                   self.config.rgb_feedback.sample_max_age_seconds, low, high)
        return {"temperature": temperature} if temperature is not None else {}


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
