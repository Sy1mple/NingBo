"""烟雾 UART：FFAA 原始浓度，支持 raw/旧百分比 decoder 的显式注入。"""
import logging
import math
import threading
import time

from experiments.smoke.jetson.smoke_protocol import SmokeFrameParser

LOGGER = logging.getLogger(__name__)


class SmokeDriver:
    def __init__(self, config, serial_factory=None, decoder=None, clock=None, wall_clock=None):
        self.config = config
        self.serial_factory = serial_factory
        self.decoder = decoder
        self.serial = None
        self.lock = threading.RLock()
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.parser = SmokeFrameParser() if config.protocol == "ffaa" and decoder is None else None
        self._concentration = None
        self._last_valid = None
        self._sample_time = None
        self._opened_at = None

    def start(self):
        with self.lock:
            self._start()

    def _start(self):
        if self.serial is None:
            if self.serial_factory is None:
                from serial import Serial
                self.serial_factory = Serial
            self.serial = self.serial_factory(
                port=self.config.port, baudrate=self.config.baudrate,
                bytesize=self.config.bytesize, parity=self.config.parity, stopbits=self.config.stopbits, timeout=self.config.timeout,
                write_timeout=self.config.timeout, exclusive=True,
            )
            self._opened_at = self.clock()
            LOGGER.info("烟雾串口已打开：%s，%s 8N1，协议=%s", self.config.port, self.config.baudrate, self.config.protocol)

    def read(self):
        with self.lock:
            if self.serial is None:
                self.start()
            try:
                if self.parser is not None:
                    return self._read_frames()
                count = min(max(self.serial.in_waiting, 1), self.config.read_size)
                raw = self.serial.read(count)
            except Exception:
                self.close()
                raise
            result = {
                "online": bool(raw), "raw": raw.hex() if raw else None,
                "percent": None, "port": self.config.port, "baud": self.config.baudrate,
                "decoder_configured": self.decoder is not None,
            }
            if raw and self.decoder is not None:
                # decoder 负责维护分包缓冲并验证协议，只能返回已校验的百分比或 None。
                value = self.decoder(raw)
                if value is not None:
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                        raise ValueError("烟雾 decoder 返回的 percent 必须在 0~100")
                    result["percent"] = value
            return result

    def _read_frames(self):
        raw = bytearray()
        deadline = self.clock() + self.config.timeout
        while len(raw) < self.config.read_size:
            count = min(max(self.serial.in_waiting, 1), self.config.read_size - len(raw))
            chunk = self.serial.read(count)
            if not chunk:
                break
            raw.extend(chunk)
            values = self.parser.feed(chunk)
            if values:
                if self._concentration is None:
                    LOGGER.info("烟雾有效帧已接收：%s；原始浓度=%d，单位未标定",
                                self.parser.last_frame.hex(' '), values[-1])
                self._concentration = values[-1]
                self._last_valid = self.clock()
                self._sample_time = self.wall_clock()
            if self.clock() >= deadline or values and not self.serial.in_waiting:
                break
        now = self.clock()
        fresh = self._last_valid is not None and 0 <= now - self._last_valid <= self.config.stale_after_seconds
        if fresh:
            status, note = "normal", "FF AA / 3C 帧格式通过；原始浓度单位未标定"
        elif now - self._opened_at < self.config.stale_after_seconds and self._last_valid is None:
            status, note = "unknown", "等待完整 FF AA H L 3C 烟雾帧"
        else:
            status, note = "error", "烟雾有效帧超时，请检查供电、共地、TX/RX 和波特率"
        return {"protocol": "ffaa", "concentration": self._concentration if fresh else None,
                "unit": "raw", "percent": None, "online": fresh, "status": status, "note": note,
                "raw": raw.hex() if raw else None,
                "last_frame": self.parser.last_frame.hex() if self.parser.last_frame else None,
                "sample_time": self._sample_time, "decoder_configured": True,
                "port": self.config.port, "baud": self.config.baudrate, **self.parser.statistics()}

    def close(self):
        with self.lock:
            if self.serial is not None:
                self.serial.close()
                self.serial = None
            self._concentration = self._last_valid = self._sample_time = self._opened_at = None
            if self.parser is not None:
                self.parser.reset()
