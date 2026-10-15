"""使用项目驱动实测 ADS1115 四路电压，不启动执行器。"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from JETSON.Sensor.Config import Ads1115Config
from JETSON.Sensor.resources import I2cResource
from JETSON.Sensor.sensors.ads1115 import Ads1115Monitor

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bus', type=int, default=8)
    parser.add_argument('--address', type=lambda x: int(x, 0), default=0x48)
    parser.add_argument('--samples', type=int, default=5)
    args = parser.parse_args()
    if args.address not in range(0x48, 0x4c) or args.samples < 1:
        parser.error('invalid ADS1115 address or sample count')
    resource = I2cResource(args.bus)
    monitor = Ads1115Monitor(resource, replace(Ads1115Config(), address=args.address, enabled=True))
    try:
        for index in range(args.samples):
            print(json.dumps({'sample': index + 1, **monitor.read_all()}), flush=True)
            if index + 1 < args.samples:
                time.sleep(1)
    finally:
        resource.close()

if __name__ == '__main__':
    main()
