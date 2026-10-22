"""Fixed RGB status text VP; glyphs select phrases in font 100."""
from .status_addresses import RGB_STATUS_RESULT_ADDR

STATUS_TEXT = {
    "unknown": "坏", "normal": "好", "error": "坏", "unconfigured": "缺",
    "disabled": "停", "checking": "检", "idle": "待",
}


def build_status_update(name, address, status):
    if name != "rgb" or address != RGB_STATUS_RESULT_ADDR:
        raise ValueError("指示灯状态结果设备或地址不匹配")
    if status not in STATUS_TEXT:
        raise ValueError("未知指示灯状态: " + status)
    text = STATUS_TEXT[status]
    return {"name": "diagnostics.rgb", "address": address,
            "data": text.encode("gb2312"), "text": text}
