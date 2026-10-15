"""舵机 A0 电压关联实测：仅打开舵机 PWM 和 ADC，主程序必须先停止。"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import time
from statistics import median
from JETSON.Sensor import DEFAULT_CONFIG
from JETSON.Sensor.resources import GpioResource, I2cResource
from JETSON.Sensor.actuators.servo import ServoDriver
from JETSON.Sensor.sensors.ads1115 import Ads1115Monitor

def main():
    cfg = DEFAULT_CONFIG
    gpio = GpioResource(cfg)
    i2c = I2cResource(cfg.i2c_bus)
    adc = Ads1115Monitor(i2c, cfg.ads1115)
    servo = ServoDriver(gpio, cfg.servo)
    report = {"bus": cfg.i2c_bus, "address": hex(cfg.ads1115.address),
              "channel": 0, "signal_type": "unconfirmed", "stages": []}
    def sample(stage, angle=None):
        started = time.monotonic()
        values = []
        errors = []
        while time.monotonic() - started < 4:
            try:
                item = adc.read_channel(0)
            except Exception as exc:
                errors.append({"elapsed": round(time.monotonic()-started,3), "error": str(exc)})
                time.sleep(.05)
                continue
            item["elapsed"] = round(time.monotonic() - started, 3)
            values.append(item)
            time.sleep(.01)
        volts = [v["volts"] for v in values]
        result = {"stage": stage, "command_angle": angle, "count": len(values),
                  "minimum_volts": min(volts) if volts else None, "median_volts": median(volts) if volts else None,
                  "maximum_volts": max(volts) if volts else None, "errors": errors, "pwm": servo.snapshot(), "samples": values}
        report["stages"].append(result)
        print(json.dumps({k:v for k,v in result.items() if k != "samples"},
                         ensure_ascii=False), flush=True)
    try:
        gpio.open()
        servo.start()
        sample("pwm_off_before")
        for angle in (45, 90, 135, 90):
            print("COMMAND_ANGLE=" + str(angle), flush=True)
            servo.set(angle)
            sample("move_and_hold", angle)
        servo.stop()
        sample("pwm_off_after")
    finally:
        try:
            servo.close()
        finally:
            gpio.close()
            i2c.close()
            target = Path("/home/dianxin/usb-hub-test/servo-a0-test.json")
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print("REPORT=" + str(target), flush=True)

if __name__ == "__main__":
    main()
