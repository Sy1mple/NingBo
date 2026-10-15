"""FF AA H L 3C 流式解析；协议不含校验和，浓度单位尚未标定。"""


class SmokeFrameParser:
    HEADER = b"\xff\xaa"
    TAIL = 0x3C

    def __init__(self):
        self.buffer = bytearray()
        self.valid_frames = 0
        self.tail_errors = 0
        self.discarded_bytes = 0
        self.total_bytes = 0
        self.last_frame = None

    def reset(self):
        """重连时丢弃不完整帧，统计计数保留。"""
        self.buffer.clear()
        self.last_frame = None

    def feed(self, data):
        values = []
        for byte in data:
            self.total_bytes += 1
            self.buffer.append(byte)
            while self.buffer and (self.buffer[0] != 0xFF
                                   or len(self.buffer) >= 2 and self.buffer[:2] != self.HEADER):
                del self.buffer[0]
                self.discarded_bytes += 1
            if len(self.buffer) < 5:
                continue
            if self.buffer[4] == self.TAIL:
                self.last_frame = bytes(self.buffer)
                values.append((self.buffer[2] << 8) | self.buffer[3])
                self.valid_frames += 1
                self.buffer.clear()
            else:
                self.tail_errors += 1
                del self.buffer[0]  # 保留其余字节，下一轮从内部帧头恢复同步。
                self.discarded_bytes += 1
        return values

    def statistics(self):
        return {"valid_frames": self.valid_frames, "tail_errors": self.tail_errors,
                "discarded_bytes": self.discarded_bytes, "total_bytes": self.total_bytes}
