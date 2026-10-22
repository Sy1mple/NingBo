"""smoke: owns driver construction, sampling/control, and its shutdown steps."""
from copy import deepcopy
import logging
import threading
import time
from .driver import SmokeDriver

READER_EMPTY_VALUES = {'smoke': {'online': None, 'raw': None, 'percent': None, 'concentration': None}}
READER_CONFIG_NAMES = {'smoke': 'smoke'}
ACTUATORS = ()
LOGGER = logging.getLogger(__name__)


class Hardware:
    def __init__(self, owner, **options):
        self.owner = owner
        cfg = owner.config
        self._readers, self._outputs = {}, {}
        self._threads, self._ready_outputs = [], set()
        self._reader_session_lock = threading.RLock()
        self._reader_clients = {}
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
            states[name] = {**values, "status": "idle", "note": "等待烟雾检测开始", "sample_time": None}
            self._reader_clients[name] = set()
            if name not in self._readers:
                states[name].update(status="disabled", note="config.py 中未启用")

    def initialize_readers(self):
        # 独立检测：启动即打开 ttyTHS0 并保持 auto 会话，不依赖串口屏。
        for name in list(self._readers):
            if name != 'smoke':
                continue
            try:
                self.acquire_reader(name, 'auto')
                with self.owner._lock:
                    self.owner.sensors[name].update(
                        status='unknown',
                        note='烟雾已独立启动，等待有效数据',
                        sample_time=None,
                    )
                LOGGER.info('烟雾 UART 已独立启动（auto），不再等待串口屏')
            except Exception:
                LOGGER.exception('烟雾独立启动失败')
        return None

    def acquire_reader(self, name, client):
        if name not in self._readers:
            raise ValueError(f"传感器未启用或不存在: {name}")
        client = str(client).strip()
        if not client:
            raise ValueError("串口使用方名称不能为空")
        with self._reader_session_lock:
            clients = self._reader_clients[name]
            if client in clients:
                return deepcopy(self.owner.sensors[name])
            first = not clients
            if first:
                self._readers[name].start()
            clients.add(client)
            with self.owner._lock:
                self.owner.sensors[name] = {
                    **READER_EMPTY_VALUES[name], "status": "unknown",
                    "note": "烟雾串口已打开，等待有效数据", "sample_time": None,
                }
            LOGGER.info("烟雾串口使用方已加入：%s；当前=%s", client, sorted(clients))
            return deepcopy(self.owner.sensors[name])

    def release_reader(self, name, client):
        if name not in self._readers:
            return deepcopy(self.owner.sensors[name])
        client = str(client).strip()
        with self._reader_session_lock:
            clients = self._reader_clients[name]
            clients.discard(client)
            if not clients:
                self._readers[name].close()
                with self.owner._lock:
                    self.owner.sensors[name] = {
                        **READER_EMPTY_VALUES[name], "status": "idle",
                        "note": "烟雾检测结束，串口已释放", "sample_time": None,
                    }
                LOGGER.info("烟雾串口已释放；最后使用方=%s", client)
            else:
                LOGGER.info("烟雾串口使用方已退出：%s；保留=%s", client, sorted(clients))
            return deepcopy(self.owner.sensors[name])

    def reconnect_reader(self, name, client):
        """保持检测会话存在，重建 UART 句柄并清空旧解析缓冲。"""
        if name not in self._readers:
            raise ValueError(f"传感器未启用或不存在: {name}")
        client = str(client).strip()
        with self._reader_session_lock:
            clients = self._reader_clients[name]
            if client not in clients:
                raise RuntimeError(f"{client} 尚未持有 {name} 串口")
            self._readers[name].close()
            # 给 tty 驱动一个真实的关闭窗口，再创建全新的 pyserial 句柄。
            time.sleep(0.2)
            self._readers[name].start()
            with self.owner._lock:
                self.owner.sensors[name] = {
                    **READER_EMPTY_VALUES[name], "status": "unknown",
                    "note": "烟雾 UART 已自动重连，继续等待有效数据", "sample_time": None,
                }
            LOGGER.warning("烟雾 UART 自动重连完成；请求方=%s；当前使用方=%s", client, sorted(clients))
            return deepcopy(self.owner.sensors[name])

    def start_sampling(self):
        for name in self._readers:
            worker = threading.Thread(target=self._sensor_loop, args=(name,), name="sensor-" + name, daemon=True)
            self._threads.append(worker)
            worker.start()

    def close_readers(self):
        errors = {}
        with self._reader_session_lock:
            for clients in self._reader_clients.values():
                clients.clear()
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
            if not self.owner._pintest_mode and self._reader_clients[name]:
                self.poll_sensor(name)
            if self.owner._stop.wait(interval):
                break

    def poll_sensor(self, name):
        states = self.owner.sensors
        if name not in self._readers:
            return deepcopy(states[name])
        # 会话锁覆盖“判断正在使用 -> read”的全过程，确保最后一次 release
        # 完成后，后台线程不会在 close 之后又通过 driver.read() 自动重开串口。
        with self._reader_session_lock:
            if not self._reader_clients[name]:
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

