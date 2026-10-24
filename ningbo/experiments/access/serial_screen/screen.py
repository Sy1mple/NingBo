"""access: scene-owned button decoding and display formatting."""
from .addresses import (BUTTON_EVENT_MAP, INPUT_EVENT_MAP,
                        LOCK_STATUS_RESULT_ADDR, LOCK_STATUS_RESULT_TEXT_BYTES)


STATUS_TEXT = {
    "unknown": "坏", "normal": "好", "error": "坏", "unconfigured": "缺",
    "disabled": "停", "checking": "检", "idle": "待",
}


def build_status_update(name, address, status):
    """Font 100 renders one Word as 检测中/正常/异常 at fixed VP 0x6010."""
    if name != "lock" or address != LOCK_STATUS_RESULT_ADDR:
        raise ValueError("门锁状态结果设备或地址不匹配")
    if status not in STATUS_TEXT:
        raise ValueError("未知门锁状态: " + status)
    text = STATUS_TEXT[status]
    encoded = text.encode("gb2312")
    if len(encoded) > LOCK_STATUS_RESULT_TEXT_BYTES:
        raise ValueError("门锁状态文字超过串口屏控件长度")
    data = encoded.ljust(LOCK_STATUS_RESULT_TEXT_BYTES, b"\xff")
    return {"name": "diagnostics.lock", "address": address,
            "data": data, "text": text}


def normalize_legacy_status_frame(frame):
    """Allow an already running backend to use font 100 without a UART restart.

    The backend IPC cache can split legacy full text into individual Words.
    Only its first Word at the lock VP selects the visible status phrase.
    """
    if frame[4:6] != LOCK_STATUS_RESULT_ADDR.to_bytes(2, "big"):
        return frame
    first_word = frame[6:8]
    replacement = {"正".encode("gb2312"): "好", "异".encode("gb2312"): "坏"}.get(first_word)
    if replacement is None:
        return frame
    return b"\x5a\xa5\x05\x82" + frame[4:6] + replacement.encode("gb2312")


def decode_event(address, value, frame=b""):
    name = BUTTON_EVENT_MAP.get((address, value), INPUT_EVENT_MAP.get(address))
    if name is None:
        return None
    return {"event": name, "address": address, "value": value,
            "hex": frame.hex(" ").upper()}


FIELDS = {}

def build_updates(data, voice_config=None):
    raise ValueError("No display fields are defined for access")
