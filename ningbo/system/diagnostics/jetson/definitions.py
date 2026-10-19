"""状态检测设备分组与汇总规则。"""

SENSOR_TARGETS = ("dht11", "bh1750", "ultrasonic", "smoke", "body_temperature", "current")
ACTUATOR_TARGETS = ("servo", "stepper", "buzzer", "rgb", "fan", "lock")
TARGETS = SENSOR_TARGETS + ACTUATOR_TARGETS
ALIASES = {"light": "bh1750", "body_temp": "body_temperature", "ads1115": "current"}


def overall_status(results):
    statuses = {result["status"] for result in results.values()}
    for status in ("error", "checking", "unconfigured", "unknown", "idle", "normal", "disabled"):
        if status in statuses:
            return status
    return "unknown"
