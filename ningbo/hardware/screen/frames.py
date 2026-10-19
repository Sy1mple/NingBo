"""解析串口屏下发的 JGUS/DGUS 变量回传，识别屏幕按钮事件。"""

from screen_registry import decode_event


class ScreenEventParser:
    """增量解析 ``5A A5`` 串口帧，兼容 0x83 读变量回传和 0x82 写变量回传。"""

    HEADER = b"\x5A\xA5"

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, data):
        """传入本次读取的 bytes，返回已完成的事件列表。"""
        self._buffer.extend(data)
        events = []
        while True:
            start = self._buffer.find(self.HEADER)
            if start < 0:
                # 串口分包可能恰好停在帧头第一个字节，保留它等待下一次读取。
                if self._buffer.endswith(self.HEADER[:1]):
                    self._buffer[:] = self.HEADER[:1]
                else:
                    self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 3:
                break
            frame_size = 3 + self._buffer[2]
            if len(self._buffer) < frame_size:
                break
            frame = bytes(self._buffer[:frame_size])
            del self._buffer[:frame_size]
            events.extend(self._parse_frame(frame))
        return events

    @staticmethod
    def _parse_frame(frame):
        payload = frame[3:]
        if len(payload) < 5 or payload[0] not in (0x82, 0x83):
            return []
        address = (payload[1] << 8) | payload[2]
        if payload[0] == 0x83:
            data_start = 4
            data_end = data_start + payload[3] * 2
            if data_end != len(payload):
                return []
        else:
            data_start = 3
            data_end = len(payload)
        if (data_end - data_start) % 2:
            return []
        values = [
            (payload[index] << 8) | payload[index + 1]
            for index in range(data_start, data_end - 1, 2)
        ]
        return [ScreenEventParser._make_event(address + index, value, frame) for index, value in enumerate(values)]

    @staticmethod
    def _make_event(address, value, frame):
        return decode_event(address, value, frame)
