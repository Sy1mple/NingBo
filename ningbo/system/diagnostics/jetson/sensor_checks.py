"""兼容传感器检测入口；实际判断由所属场景或 ADC 硬件模块提供。"""
from experiments.environment.jetson.diagnostics import check_dht11, check_bh1750
from experiments.ultrasonic.jetson.diagnostics import check_ultrasonic
from experiments.smoke.jetson.diagnostics import check_smoke
from experiments.body_temperature.jetson.diagnostics import check_body_temperature
from hardware.adc.diagnostics import check_current

SENSOR_CHECKS = {
    "dht11": check_dht11, "bh1750": check_bh1750,
    "ultrasonic": check_ultrasonic, "smoke": check_smoke,
    "body_temperature": check_body_temperature, "current": check_current,
}


class SensorStatusChecker:
    def __init__(self, manager):
        self.manager = manager

    def check(self, name):
        return SENSOR_CHECKS[name](self.manager)
