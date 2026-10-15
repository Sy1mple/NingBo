"""Temporary continuous-forward stepper test. HMI stepper.stop remains effective."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime
import json
import logging
import os
import signal
import time
from application import NingBoApplication
from JETSON.Sensor import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
config = DEFAULT_CONFIG
app = NingBoApplication(config)
for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, lambda signum, frame: app.request_stop())
status_path = Path("logs/stepper_forward_test.json")
next_status = 0.0
try:
    with app:
        app.devices.control("stepper", dir_="fwd", n=config.stepper.maximum_steps, spd=config.stepper.default_speed_ms)
        logging.info("持续正转测试启动，步间隔 %s ms；屏幕停止按钮可停止电机", config.stepper.default_speed_ms)
        while not app._shutdown.is_set():
            app.process_once()
            state = app.devices.snapshot()["actuators"]["stepper"]
            if state.get("driver_error"):
                raise RuntimeError(state["driver_error"])
            if state.get("running") and state.get("dir") == "fwd" and state["n"] < config.stepper.maximum_steps // 2:
                app.devices.control("stepper", dir_="fwd", n=config.stepper.maximum_steps, spd=config.stepper.default_speed_ms)
            now = time.monotonic()
            if now >= next_status:
                record = {"time": datetime.now().isoformat(timespec="seconds"), "mode": "continuous_forward", **state}
                try:
                    record["feedback_volts"] = app.devices.manager.adc_monitor.read_channel(1, address=0x49)["volts"]
                except Exception as exc:
                    record["feedback_error"] = str(exc)
                temporary = status_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                os.replace(str(temporary), str(status_path))
                next_status = now + 1
            app._shutdown.wait(config.screen.event_poll_interval)
finally:
    logging.info("持续正转测试入口已退出")
