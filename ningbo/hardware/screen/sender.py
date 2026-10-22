"""
screen_sender.py

作用：
把 message_parser.py 解析后的“地址 + 数值”
封装成 JGUS 写变量指令，并通过串口发送给串口屏。

JGUS 写变量格式：
5A A5 Length 82 AddrH AddrL DataH DataL

当前写入 1 个 Word（2 Byte），因此：
Length = 0x05
"""

from config import DEFAULT_CONFIG
from hardware.screen.serial_discovery import discover_screen
import time


class ScreenSender:
    def __init__(self, port=None, baudrate=None, config=None, excluded_ports=()):
        """
        port:
            Windows 示例：COM3
            Jetson 示例：/dev/ttyUSB0

        baudrate:
            当前串口屏使用 115200
        """

        self.config = config or DEFAULT_CONFIG.screen
        self.port = self.config.port if port is None else port
        self.baudrate = self.config.baudrate if baudrate is None else baudrate
        self.ser = None
        self.excluded_ports = excluded_ports
        self._pending_input = b''

    def open(self):
        """打开串口。"""

        if self.ser is not None and self.ser.is_open:
            return

        if self.port == 'auto':
            from dataclasses import replace
            config = replace(self.config, baudrate=self.baudrate)
            self.ser, self._pending_input = discover_screen(config, self.excluded_ports)
            return

        import serial
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.config.bytesize,
            parity=self.config.parity,
            stopbits=self.config.stopbits,
            timeout=self.config.read_timeout,
            write_timeout=self.config.write_timeout,
        )

    def close(self):
        """关闭串口。"""

        if self.ser is not None and self.ser.is_open:
            self.ser.close()
        self.ser = None
        self._pending_input = b''

    def take_pending_input(self):
        pending, self._pending_input = self._pending_input, b''
        return pending

    @staticmethod
    def build_write_frame(address, value):
        """
        生成 JGUS 0x82 写变量帧。

        示例：
        address = 0x1000
        value   = 2500 = 0x09C4

        返回：
        5A A5 05 82 10 00 09 C4
        """

        if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
            raise ValueError(f"地址必须是 0~65535 的整数: {address!r}")

        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise ValueError(f"数值超出 16 位范围: {value}")

        return bytes([
            0x5A,
            0xA5,
            0x05,
            0x82,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ])

    @staticmethod
    def build_write_bytes_frame(address, data):
        """一次写入多个 Word，适用于编码后的文本缓冲区。"""
        if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
            raise ValueError("地址必须是 16 位整数")
        if not isinstance(data, (bytes, bytearray)) or not data or len(data) % 2 or len(data) > 252:
            raise ValueError("文本数据须为非空、偶数字节数且不超过 252 Byte")
        if address + len(data) // 2 > 0x10000:
            raise ValueError("文本写入超出 VP 范围")
        return bytes((0x5A, 0xA5, len(data) + 3, 0x82, address >> 8, address & 0xFF)) + bytes(data)

    def _write_frame(self, frame):
        if self.ser is None or not self.ser.is_open:
            self.open()
        if self.ser.write(frame) != len(frame):
            raise OSError("串口屏数据帧未完整写入")
        self.ser.flush()
        return frame

    def read_value(self, address, timeout=.15):
        """通过本服务持有的串口回读一个 VP；保留同时收到的按钮帧。"""
        query = bytes((0x5A, 0xA5, 4, 0x83, address >> 8, address & 255, 1))
        prefix = bytes((0x5A, 0xA5, 6, 0x83, address >> 8, address & 255, 1))
        if self.ser.write(query) != len(query):
            raise OSError("串口屏读回请求未完整写入")
        pending = bytearray()
        old_timeout = self.ser.timeout
        deadline = time.monotonic() + timeout
        try:
            self.ser.timeout = .02
            while time.monotonic() < deadline:
                pending.extend(self.ser.read(min(self.ser.in_waiting or 1, 4096)))
                index = pending.find(prefix)
                if index >= 0 and len(pending) >= index + 9:
                    value = int.from_bytes(pending[index + 7:index + 9], "big")
                    del pending[index:index + 9]
                    return value
            return None
        finally:
            self.ser.timeout = old_timeout
            self._pending_input += bytes(pending)

    def write_value(self, address, value):
        """
        向一个变量地址写入 1 个 Word。
        """

        frame = self.build_write_frame(address, value)
        return self._write_frame(frame)

    def send_updates(self, updates):
        """
        接收 message_parser.py 的解析结果并逐项发送。

        updates 示例：
        [
            {
                "name": "temperature",
                "address": 0x1000,
                "value": 2536,
                "original_value": 25.36
            },
            {
                "name": "humidity",
                "address": 0x1010,
                "value": 6320,
                "original_value": 63.20
            }
        ]

        返回发送记录。
        """

        sent = []

        for item in updates:
            name = item.get("name", "unknown")
            address = item["address"]
            if "data" in item:
                frame = self._write_frame(self.build_write_bytes_frame(address, item["data"]))
                sent.append({**item, "frame": frame, "hex": frame.hex(" ").upper()})
                continue
            value = item["value"]
            frame = self.write_value(address, value)

            sent.append({
                "name": name,
                "address": address,
                "value": value,
                "frame": frame,
                "hex": frame.hex(" ").upper(),
            })

        return sent
