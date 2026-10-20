"""diagnostics: scene-owned button decoding and display formatting."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .addresses import STATUS_CHECK_BINDINGS, STATUS_RESULT_ADDRESS_MAP, STATUS_META_ADDRESS_MAP
from experiments.environment.serial_screen.addresses import (
    STATUS_RESULT_TEXT_BYTES, STATUS_RESULT_TEXT_TARGETS)
from experiments.access.serial_screen.addresses import LOCK_STATUS_RESULT_TEXT_BYTES
from experiments.environment.serial_screen.screen import build_status_update as environment_status_update
from experiments.ultrasonic.serial_screen.screen import build_status_update as ultrasonic_status_update
from hardware.rgb.status_screen import build_status_update as rgb_status_update
from experiments.smoke.serial_screen.screen import build_status_update as smoke_status_update
from experiments.servo.serial_screen.screen import build_status_update as servo_status_update
from experiments.stepper.serial_screen.screen import build_status_update as stepper_status_update
from experiments.fan.serial_screen.screen import build_status_update as fan_status_update
from experiments.access.serial_screen.screen import build_status_update as access_status_update


MAX_SCREEN_VALUE = 0x7FFF
STATUS_TEXT_BUILDERS = {
    **{name: environment_status_update for name in STATUS_RESULT_TEXT_TARGETS},
    "ultrasonic": ultrasonic_status_update,
    "buzzer": ultrasonic_status_update,
    "rgb": rgb_status_update,
    "smoke": smoke_status_update,
    "servo": servo_status_update,
    "stepper": stepper_status_update,
    "fan": fan_status_update,
    "lock": access_status_update,
}

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


def configured_status_button_events():
    from screen_registry import ADDRESS_MAP, BUTTON_EVENT_MAP
    """只注册完整且合法的状态检测按钮，拦截键值冲突。"""
    events = {}
    for event, (address, value) in STATUS_CHECK_BINDINGS.items():
        if address is None and value is None:
            continue
        if any(isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 0xFFFF
               for item in (address, value)):
            raise ValueError(f"{event} 必须同时填写合法的键值返回地址和返回值")
        key = (address, value)
        if (key in BUTTON_EVENT_MAP and BUTTON_EVENT_MAP[key] != event) or key in events:
            raise ValueError(f"{event} 的地址/返回值与已有按钮冲突")
        events[key] = event
    return events

def validate_status_result_addresses():
    from screen_registry import ADDRESS_MAP, BUTTON_EVENT_MAP
    """结果 VP 不得覆盖原业务变量或按钮 VP，也不得互相重叠。"""
    occupied = {address for address in ADDRESS_MAP.values()}
    occupied.update(address for address, _ in BUTTON_EVENT_MAP)
    for name, address in {**STATUS_RESULT_ADDRESS_MAP, **STATUS_META_ADDRESS_MAP}.items():
        if address is None:
            continue
        if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
            raise ValueError(f"{name} 的状态 VP 地址无效")
        if name in STATUS_RESULT_TEXT_TARGETS:
            words = STATUS_RESULT_TEXT_BYTES // 2
        elif name == "lock":
            words = LOCK_STATUS_RESULT_TEXT_BYTES // 2
        else:
            words = 1
        if address + words > 0x10000:
            raise ValueError(f"{name} 的状态文本超出 VP 范围")
        span = set(range(address, address + words))
        if occupied & span:
            raise ValueError(f"{name} 的状态 VP 与已有变量、按钮或结果地址冲突")
        occupied.update(span)
    return True


def build_updates(data, voice_config=None):
    """状态结果写入已配置 VP；预留的 None 一律跳过。"""
    import screen_registry as addresses
    addresses.validate_status_result_addresses()
    updates = []
    statuses = data.get("statuses", {})
    if not isinstance(statuses, dict):
        raise ValueError("statuses 必须是设备名到状态名的映射")
    for name, status in statuses.items():
        if name not in addresses.STATUS_RESULT_ADDRESS_MAP:
            raise ValueError(f"未知状态检测设备: {name}")
        if status not in addresses.STATUS_CODE_MAP:
            raise ValueError(f"未知状态名: {status}")
        address = addresses.STATUS_RESULT_ADDRESS_MAP[name]
        if address is not None:
            if name in STATUS_TEXT_BUILDERS:
                updates.append(STATUS_TEXT_BUILDERS[name](name, address, status))
            else:
                updates.append(make_update(f"diagnostics.{name}", address, addresses.STATUS_CODE_MAP[status]))
    for field in ("busy", "progress", "overall"):
        if field not in data:
            continue
        value = data[field]
        if field == "overall":
            if value not in addresses.STATUS_CODE_MAP:
                raise ValueError(f"未知总体状态: {value}")
            value = addresses.STATUS_CODE_MAP[value]
        elif field == "busy":
            if value not in (True, False, 0, 1):
                raise ValueError("busy 必须是 0/1 或布尔值")
            value = int(value)
        elif isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError("progress 必须是 0~100 的整数")
        address = addresses.STATUS_META_ADDRESS_MAP[field]
        if address is not None:
            updates.append(make_update(f"diagnostics.{field}", address, value))
    for item in updates:
        address = item["address"]
        if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
            raise ValueError("状态结果 VP 必须是 0~65535 的整数")
    return updates
