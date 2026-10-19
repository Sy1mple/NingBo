"""environment: interpret real device snapshots without inventing sensor data."""
import logging
import time

LOGGER = logging.getLogger(__name__)

class SnapshotReader:
    def __init__(self, config, wall_clock=None, light_full_scale_lux=None):
        self.config = config
        self.wall_clock = wall_clock or time.time
        self.light_full_scale_lux = light_full_scale_lux or config.light.display_full_scale_lux

    def values(self, snapshot):
        sensors = snapshot.get("sensors", {})
        out = {}
        dht = sensors.get("dht11") or {}
        light = sensors.get("bh1750") or {}
        if dht.get("temp") is not None:
            out["temperature"] = dht["temp"]
        if dht.get("humidity") is not None:
            out["humidity"] = dht["humidity"]
        if light.get("lux") is not None:
            out["light"] = max(0, min(100, round(light["lux"] * 100 / self.light_full_scale_lux)))
        return out
