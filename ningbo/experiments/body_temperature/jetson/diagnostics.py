"""体温场景：驱动配置与测温范围检查。"""
from hardware.diagnostics import check_numeric_sensor


def check_body_temperature(manager):
    if "body_temperature" not in getattr(manager, "_readers", {}):
        return {"status": "unconfigured", "note": "人体温度型号及驱动尚未提供"}
    low, high = manager.config.body_temperature.temperature_range
    return check_numeric_sensor(manager, "body_temperature", ("temperature",),
                                lambda state: low <= state["temperature"] <= high)
