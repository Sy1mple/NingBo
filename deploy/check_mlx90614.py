"""只读采样 GY-906 / MLX90614，不启动其他设备或修改传感器 EEPROM。"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Sensor.resources import I2cResource
from JETSON.Sensor.sensors.mlx90614 import Mlx90614Driver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=DEFAULT_CONFIG.i2c_bus)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=DEFAULT_CONFIG.body_temperature.address)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if args.bus < 0 or not 0x08 <= args.address <= 0x77 or not 1 <= args.samples <= 1000:
        parser.error("总线、I2C 地址或采样次数无效")
    config = replace(DEFAULT_CONFIG.body_temperature, address=args.address)
    resource = I2cResource(args.bus)
    driver = Mlx90614Driver(resource, config)
    try:
        for index in range(args.samples):
            print(json.dumps({"sample": index + 1, **driver.read()}, ensure_ascii=False), flush=True)
            if index + 1 < args.samples:
                time.sleep(config.interval)
    finally:
        resource.close()


if __name__ == "__main__":
    main()
