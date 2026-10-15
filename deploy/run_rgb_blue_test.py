from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import signal
import threading
import json
from JETSON.Sensor import DEFAULT_CONFIG as c
from JETSON.Sensor.resources import GpioResource
from JETSON.Sensor.actuators.outputs import RgbDriver
stop = threading.Event()
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(sig, lambda signum, frame: stop.set())
g = GpioResource(c)
led = RgbDriver(g, c.rgb)
try:
    g.open()
    led.start()
    led.set(color="blue")
    print(json.dumps({"color":"blue", "gpio_levels":{str(p):g.read(p) for p in c.rgb.pins}}), flush=True)
    stop.wait()
finally:
    try:
        led.stop()
    finally:
        g.close()
    print("RGB_TEST_STOPPED", flush=True)
