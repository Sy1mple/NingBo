"""ultrasonic: scene-owned button decoding and display formatting."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .addresses import BUTTON_EVENT_MAP, INPUT_EVENT_MAP
from . import addresses


MAX_SCREEN_VALUE = 0x7FFF

# Each code selects a whole status phrase in the screen's 100号专用字库.
STATUS_TEXT = {
    "unknown": "待", "normal": "好", "error": "坏",
    "unconfigured": "缺", "disabled": "停", "checking": "检", "idle": "待",
}


def build_status_update(name, address, status):
    """Ultrasonic/buzzer fixed text VPs receive one Word, without a terminator."""
    targets = {"ultrasonic": addresses.ULTRASONIC_STATUS_RESULT_ADDR,
               "buzzer": addresses.BUZZER_STATUS_RESULT_ADDR}
    if name not in targets or address != targets[name]:
        raise ValueError("超声波或蜂鸣器状态结果设备或地址不匹配")
    if status not in STATUS_TEXT:
        raise ValueError("未知超声波状态: " + status)
    text = STATUS_TEXT[status]
    return {"name": "diagnostics." + name, "address": address,
            "data": text.encode("gb2312"), "text": text}

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
    ('distance', addresses.ULTRASONIC_DISTANCE, 10),
)

def build_updates(data, voice_config=None):
    updates = [make_update(name, address, data[name], scale)
               for name, address, scale in FIELDS if name in data]
    if not updates:
        raise ValueError("ultrasonic message contains no display fields")
    return updates
