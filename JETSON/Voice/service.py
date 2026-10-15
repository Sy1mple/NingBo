"""USB 采音与识别线程；结果排队，屏幕写入由工程主循环执行。"""
from collections import deque
from copy import deepcopy
import threading
import time
import logging

from JETSON.Sensor.sensors.usb_microphone import UsbMicrophone
from .recognizer import RecognizerFactory

LOGGER = logging.getLogger(__name__)


class VoiceService:
    def __init__(self, config, join_timeout=5.0, microphone_factory=None, recognizer_factory=None):
        self.config = config
        self.join_timeout = join_timeout
        self.microphone_factory = microphone_factory or UsbMicrophone
        self.recognizer_factory = recognizer_factory or RecognizerFactory()
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.RLock()
        self._worker = None
        self._audio = None
        self._cancel = threading.Event()
        self._session = 0
        self._sequence = 0
        self._results = deque(maxlen=config.result_queue_size)
        self._state = {"phase": "idle", "listening": False, "text": "", "error": "", "device": "", "output_device": "", "backend": config.backend}

    def start(self):
        with self._lifecycle_lock:
            if not self.config.enabled:
                return {"accepted": False, "note": "语音功能在 Config.py 中未启用"}
            if self._worker is not None and self._worker.is_alive():
                return {"accepted": False, "note": "语音任务正在启动、聆听或停止中"}
            with self._lock:
                self._session += 1
                session = self._session
                self._cancel = threading.Event()
                self._results.clear()
                self._state.update(phase="starting", listening=True, text="", error="", device="", output_device="")
            self._worker = threading.Thread(target=self._run, args=(session, self._cancel), name="usb-voice", daemon=True)
            try:
                self._worker.start()
            except Exception:
                self._worker = None
                with self._lock:
                    self._state.update(phase="error", listening=False, error="无法启动语音线程")
                raise
            return {"accepted": True, "session_id": session, "note": "正在初始化 USB 麦克风和本地识别模型"}

    def _run(self, session, cancel):
        audio = None
        engine = None
        error = ""
        try:
            audio = self.microphone_factory(self.config)
            with self._lock:
                self._audio = audio
            sample_rate = audio.prepare()
            LOGGER.info("语音输入设备：%s，采样率 %s Hz；初始化 %s", audio.device_name, sample_rate, self.config.backend)
            if cancel.is_set():
                return
            engine = self.recognizer_factory(self.config, sample_rate)
            if cancel.is_set():
                return
            audio.start()
            LOGGER.info("语音采音已就绪，可以开始说话：%s", audio.device_name)
            with self._lock:
                if session == self._session and not cancel.is_set():
                    self._state.update(phase="listening", device=audio.device_name,
                                       output_device=getattr(audio, 'output_device_name', ''))
            last_partial, last_time = "", 0.0
            while not cancel.is_set():
                pcm = audio.read(timeout=self.config.read_timeout)
                if not pcm or cancel.is_set():
                    continue
                result = engine.process(pcm)
                text, final = result["text"], result["final"]
                if not isinstance(text, str) or not isinstance(final, bool):
                    raise ValueError("语音结果格式无效")
                if not text:
                    continue
                now = time.monotonic()
                if not final:
                    if not self.config.show_partial or text == last_partial or now - last_time < self.config.partial_interval:
                        continue
                    last_partial, last_time = text, now
                else:
                    last_partial = ""
                with self._lock:
                    if session != self._session or cancel.is_set():
                        continue
                    self._sequence += 1
                    self._results.append({"session_id": session, "sequence": self._sequence, "text": text, "final": final})
                    self._state["text"] = text
                    LOGGER.info("语音识别完成：%d 字，final=%s", len(text), final)
        except Exception as exc:
            error = str(exc)
        finally:
            if engine is not None and hasattr(engine, 'close'):
                try:
                    engine.close()
                except Exception as exc:
                    error = error or str(exc)
            if audio is not None:
                try:
                    audio.close()
                except Exception as exc:
                    error = error or str(exc)
            with self._lock:
                self._audio = None
                if session == self._session:
                    self._state.update(phase="error" if error else "idle", listening=False, error=error)
                    if error:
                        self._results.clear()

    def poll_results(self):
        with self._lock:
            results = list(self._results)
            self._results.clear()
            return results

    def snapshot(self):
        with self._lock:
            return {**deepcopy(self._state), "session_id": self._session,
                    "worker_alive": bool(self._worker and self._worker.is_alive())}

    def stop(self):
        with self._lifecycle_lock:
            with self._lock:
                self._session += 1
                self._cancel.set()
                self._results.clear()
                self._state.update(phase="stopping", listening=False, text="")
                audio = self._audio
            errors = []
            if audio is not None:
                try:
                    audio.close()
                except Exception as exc:
                    errors.append(str(exc))
            if self._worker is not None:
                self._worker.join(self.join_timeout)
                if self._worker.is_alive():
                    errors.append("语音线程未结束，暂时禁止再次启动")
                else:
                    self._worker = None
            with self._lock:
                self._state.update(phase="error" if errors else "idle", error="; ".join(errors))
            if errors:
                raise RuntimeError("语音停止失败: " + "; ".join(errors))
            return {"accepted": True, "note": "USB 麦克风聆听已停止"}
