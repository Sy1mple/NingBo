"""只读采样物理 8/10 脚烟雾串口；运行前停止占用该串口的主服务。"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from JETSON.Sensor import DEFAULT_CONFIG
from JETSON.Sensor.sensors.smoke import SmokeDriver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default=DEFAULT_CONFIG.smoke.port)
    parser.add_argument('--baudrate', type=int, default=9600)
    parser.add_argument('--samples', type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000 or args.baudrate <= 0:
        parser.error('采样次数或波特率无效')
    config = replace(DEFAULT_CONFIG.smoke, port=args.port, baudrate=args.baudrate, protocol='ffaa')
    replace(DEFAULT_CONFIG, smoke=config).validate()
    driver = SmokeDriver(config)
    valid = False
    try:
        for index in range(args.samples):
            state = driver.read()
            valid = valid or state['status'] == 'normal'
            print(json.dumps({'sample': index + 1, **state}, ensure_ascii=False), flush=True)
            if index + 1 < args.samples:
                time.sleep(config.interval)
    finally:
        driver.close()
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
