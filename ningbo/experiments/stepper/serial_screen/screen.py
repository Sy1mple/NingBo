"""步进方向沿用舵机 GB2312 文本写法，步数使用单 Word 数值。"""
import logging
from .addresses import BUTTON_EVENT_MAP, INPUT_EVENT_MAP
from . import addresses

LOGGER = logging.getLogger(__name__)
_warned_layouts = set()

STATUS_TEXT = {
    "unknown": "待", "normal": "好", "error": "坏", "unconfigured": "缺",
    "disabled": "停", "checking": "检", "idle": "待",
}


def build_status_update(name, address, status):
    """One Word selects the full status phrase from font 100 at fixed VP 0x6006."""
    if name != "stepper" or address != addresses.STEPPER_STATUS_RESULT_ADDR:
        raise ValueError("步进状态结果设备或地址不匹配")
    if status not in STATUS_TEXT:
        raise ValueError("未知步进状态: " + status)
    text = STATUS_TEXT[status]
    return {"name": "diagnostics.stepper", "address": address,
            "data": text.encode("gb2312"), "text": text}


def decode_event(address, value, frame=b""):
    name = BUTTON_EVENT_MAP.get((address, value), INPUT_EVENT_MAP.get(address))
    if name is None:
        return None
    return {"event": name, "address": address, "value": value,
            "hex": frame.hex(" ").upper()}


def build_updates(data, voice_config=None):
    updates = []
    if 'direction' in data:
        text = data['direction']
        if text not in ('正转', '反转', '停止'):
            raise ValueError('步进方向文本必须是正转、反转或停止')
        encoded = text.encode('gb2312') + b'\xff\xff'
        # 保留用户指定 VP，但明确报告当前布局的冲突，不能把帧测试通过当成屏幕显示通过。
        import screen_registry
        begin, end = addresses.STEPPER_DIRECTION, addresses.STEPPER_DIRECTION + len(encoded) // 2
        conflicts = tuple((name, vp) for name, vp in screen_registry.ADDRESS_MAP.items()
                          if name != 'stepper.direction' and begin <= vp < end)
        layout = (begin, conflicts)
        if conflicts and layout not in _warned_layouts:
            _warned_layouts.add(layout)
            LOGGER.warning('步进中文方向 VP 0x%04X~0x%04X 与 %s 重叠；当前地址定义无法保证同时正确显示',
                           begin, end - 1, ', '.join('%s=0x%04X' % item for item in conflicts))
        updates.append(dict(name='stepper.direction', address=begin, data=encoded, text=text))
    if 'steps' in data:
        value = data['steps']
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            raise ValueError('步进步数显示必须是 0~65535 的单个 Word')
        updates.append(dict(name='stepper.steps', address=addresses.STEPPER_NUMBER, value=value, original_value=value))
    if not updates:
        raise ValueError('stepper message contains no display fields')
    return updates
