"""smoke: owns driver construction, sampling/control, and its shutdown steps."""
from copy import deepcopy
import threading
import time
from .driver import SmokeDriver

READER_EMPTY_VALUES = {'smoke': {'online': None, 'raw': None, 'percent': None, 'concentration': None}}
READER_CONFIG_NAMES = {'smoke': 'smoke'}
ACTUATORS = ()


class Hardware:
    def __init__(self, owner, **options):
        self.owner = owner
        cfg = owner.config
        self._readers, self._outputs = {}, {}
        self._threads, self._ready_outputs = [], set()
        if cfg.smoke.enabled:
            self._readers["smoke"] = SmokeDriver(cfg.smoke, decoder=options.get("smoke_decoder"))
        supplied = options.get("sensor_drivers")
        if supplied is not None:
            self._readers = {name: driver for name, driver in supplied.items() if name in READER_EMPTY_VALUES}
        supplied = options.get("output_drivers")
        if supplied is not None:
            self._outputs = {name: driver for name, driver in supplied.items() if name in ACTUATORS}
        for name, values in READER_EMPTY_VALUES.items():
            states = owner.sensors
            states[name] = {**values, "status": None, "note": "", "sample_time": None}
            if name not in self._readers:
                states[name].update(status="disabled", note="config.py 中未启用")

    def initialize_readers(self):
        for name, driver in self._readers.items():
            try:
                if hasattr(driver, "start"):
                    driver.start()
            except Exception as exc:
                self.owner._init_errors.append(f"{name}: {exc}")

    def start_sampling(self):
        for name in self._readers:
            worker = threading.Thread(target=self._sensor_loop, args=(name,), name="sensor-" + name, daemon=True)
            self._threads.append(worker)
            worker.start()

    def close_readers(self):
        errors = {}
        for name, driver in self._readers.items():
            try:
                if hasattr(driver, "close"):
                    driver.close()
            except Exception as exc:
                errors[name] = str(exc)
        return errors

    def _sensor_loop(self, name):
        interval = getattr(self.owner.config, READER_CONFIG_NAMES[name]).interval
        while not self.owner._stop.is_set():
            if not self.owner._pintest_mode:
                self.poll_sensor(name)
            if self.owner._stop.wait(interval):
                break

    def poll_sensor(self, name):
        states = self.owner.sensors
        if name not in self._readers:
            return deepcopy(states[name])
        with self.owner._sampling_condition:
            if self.owner._pintest_mode or self.owner._stop.is_set():
                return deepcopy(states[name])
            self.owner._active_reads += 1
        try:
            try:
                state = self._readers[name].read()
                online = state.get("online", True) and state.get("ok", True)
                status = "normal" if online else "error"
                note = "" if online else "本次采样无有效数据"
                if name == "smoke" and state.get("protocol") == "ffaa":
                    status, note = state["status"], state["note"]
                elif name == "smoke" and online and state.get("percent") is None:
                    status = "unknown" if state.get("decoder_configured") else "unconfigured"
                    note = "串口收到字节，尚未取得经过协议校验的测量值"
                state.update(status=status, note=note)
                state.setdefault("sample_time", time.time())
            except Exception as exc:
                state = {**READER_EMPTY_VALUES[name], "status": "error", "note": str(exc), "sample_time": time.time()}
        finally:
            with self.owner._sampling_condition:
                self.owner._active_reads -= 1
                self.owner._sampling_condition.notify_all()
        with self.owner._lock:
            states[name] = state
            return deepcopy(state)

