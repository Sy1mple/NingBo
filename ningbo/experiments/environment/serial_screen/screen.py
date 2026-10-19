"""environment: scene-owned button decoding and display formatting."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .addresses import BUTTON_EVENT_MAP, INPUT_EVENT_MAP
from . import addresses


MAX_SCREEN_VALUE = 0x7FFF

STATUS_TEXT = {
    "unknown": "待",
    "normal": "好",
    "error": "坏",
    "unconfigured": "缺",
    "disabled": "停",
    "checking": "检",
    "idle": "待",
}


def build_status_update(name, address, status):
    """One glyph occupies one Word; never overwrite the next status VP."""
    if name not in addresses.STATUS_RESULT_TEXT_TARGETS:
        raise ValueError("不是环境场景的状态检测项: " + name)
    if status not in STATUS_TEXT:
        raise ValueError("未知环境状态: " + status)
    text = STATUS_TEXT[status]
    encoded = text.encode("gb2312")
    length = addresses.STATUS_RESULT_TEXT_BYTES
    if len(encoded) != length:
        raise ValueError("相邻环境状态 VP 只允许一个两字节汉字")
    # Do not append FF FF: it would overwrite the following device's VP.
    return {"name": "diagnostics." + name, "address": address,
            "data": encoded, "text": text}

def make_update(name, address, value, scale=1):
    """生成统一的屏幕更新数据。"""

    try:
        if isinstance(value, bool):
            raise ValueError("布尔值不是数字")
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("数值必须有限")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} 不是有效数字: {value}") from exc

    raw_value = int((number * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    # 当前数值控件按 JGUS 有符号 16 位 Word 显示。
    if raw_value < 0 or raw_value > MAX_SCREEN_VALUE:
        raise ValueError(
            f"{name} 转换后的值 {raw_value} 超出屏幕数值范围 0~{MAX_SCREEN_VALUE}"
        )

    return {
        "name": name,
        "address": address,
        "value": raw_value,
        "original_value": value,
    }


def decode_event(address, value, frame=b""):
    name = BUTTON_EVENT_MAP.get((address, value), INPUT_EVENT_MAP.get(address))
    if name is None:
        return None
    return {"event": name, "address": address, "value": value,
            "hex": frame.hex(" ").upper()}


FIELDS = (
    ('temperature', addresses.ENV_TEMPERATURE, 100),
    ('humidity', addresses.ENV_HUMIDITY, 100),
    ('light', addresses.ENV_LIGHT, 1),
)

def build_updates(data, voice_config=None):
    updates = [make_update(name, address, data[name], scale)
               for name, address, scale in FIELDS if name in data]
    if not updates:
        raise ValueError("environment message contains no display fields")
    return updates
