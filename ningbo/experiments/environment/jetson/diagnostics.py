"""环境场景：温湿度与光照状态判断。"""
from hardware.diagnostics import check_numeric_sensor


def check_dht11(manager):
    config = manager.config.dht11
    return check_numeric_sensor(manager, "dht11", ("temp", "humidity"),
        lambda state: config.temperature_range[0] <= state["temp"] <= config.temperature_range[1]
        and config.humidity_range[0] <= state["humidity"] <= config.humidity_range[1])


def check_bh1750(manager):
    return check_numeric_sensor(manager, "bh1750", ("lux",), lambda state: state["lux"] >= 0)
