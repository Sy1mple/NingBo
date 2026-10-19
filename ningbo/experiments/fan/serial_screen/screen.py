"""fan: scene-owned button decoding and display formatting."""
from .addresses import BUTTON_EVENT_MAP, INPUT_EVENT_MAP
from .addresses import FAN_STATUS_RESULT_ADDR

STATUS_TEXT = {
    "unknown": "待", "normal": "好", "error": "坏", "unconfigured": "缺",
    "disabled": "停", "checking": "检", "idle": "待",
}


def build_status_update(name, address, status):
    """Fixed 0x6009 text uses one Word and the dedicated phrase font 100."""
    if name != "fan" or address != FAN_STATUS_RESULT_ADDR:
        raise ValueError("风扇状态结果设备或地址不匹配")
    if status not in STATUS_TEXT:
        raise ValueError("未知风扇状态: " + status)
    text = STATUS_TEXT[status]
    return {"name": "diagnostics.fan", "address": address,
            "data": text.encode("gb2312"), "text": text}


def decode_event(address, value, frame=b""):
    name = BUTTON_EVENT_MAP.get((address, value), INPUT_EVENT_MAP.get(address))
    if name is None:
        return None
    return {"event": name, "address": address, "value": value,
            "hex": frame.hex(" ").upper()}


FIELDS = {}

def build_updates(data, voice_config=None):
    raise ValueError("No display fields are defined for fan")
