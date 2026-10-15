"""
message_parser.py

作用：
接收开发板各场景程序发送过来的业务消息，
解析出“应该更新串口屏哪个地址、写入什么值”。

注意：
- 本文件只负责解析消息，不负责打开串口；
- JGUS 的 5A A5 / 0x82 等协议封装交给 screen_sender.py；
- 所有变量地址统一从 addresses.py 引用。
"""

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from Serial_Screen.screen_service.screen import addresses
from JETSON.Sensor.Config import DEFAULT_CONFIG
from Serial_Screen.screen_service.screen.text_updates import voice_text_update

from Serial_Screen.screen_service.screen.addresses import (
    BODY_TEMPERATURE,
    ENV_TEMPERATURE,
    ENV_HUMIDITY,
    ENV_LIGHT,
    SMOKE_VALUE,
    ULTRASONIC_DISTANCE,
)


# ============================================================
# 数据缩放
# 温度、湿度按两位小数显示；距离按一位小数显示；光照按整数显示。
# 25.36  -> 2536
# 63.20  -> 6320
# 12.3   -> 123
# 428.00 -> 428
# ============================================================

ENV_SCALE = 100
DISTANCE_SCALE = 10
SERVO_ANGLE_SCALE = 10  # 屏幕角度控件：900 显示 90°。
LIGHT_SCALE = 1
MAX_SCREEN_VALUE = 0x7FFF

SCENE_FIELDS = {
    "environment": (
        ("temperature", ENV_TEMPERATURE, ENV_SCALE),
        ("humidity", ENV_HUMIDITY, ENV_SCALE),
        ("light", ENV_LIGHT, LIGHT_SCALE),
    ),
    "body_temperature": (("temperature", BODY_TEMPERATURE, ENV_SCALE),),
    "ultrasonic": (("distance", ULTRASONIC_DISTANCE, DISTANCE_SCALE),),
    "smoke": (("value", SMOKE_VALUE, 1),),
    "servo": (("target_angle", addresses.SERVO_TARGET_ANGLE, SERVO_ANGLE_SCALE),),
}

SCENE_ALIASES = {
    "body_temp": "body_temperature",
    "body": "body_temperature",
    "smoke_alarm": "smoke",
}


class MessageParser:
    """串口屏业务消息解析器。"""

    def __init__(self, voice_config=None):
        self.voice_config = voice_config or DEFAULT_CONFIG.voice

    def parse(self, message):
        """
        解析业务消息。

        支持两种输入：

        1. Python dict
        {
            "scene": "environment",
            "temperature": 25.36,
            "humidity": 63.20,
            "light": 428.00
        }

        2. JSON 字符串
        '{"scene":"environment","temperature":25.36,"humidity":63.20,"light":428.00}'

        返回：
        [
            {"address": 0x1000, "value": 2536, "name": "temperature"},
            {"address": 0x1010, "value": 6320, "name": "humidity"},
            {"address": 0x1020, "value": 428, "name": "light"},
        ]
        """

        data = self._to_dict(message)

        scene = data.get("scene")

        if not isinstance(scene, str) or not scene:
            raise ValueError("消息缺少有效的 scene 字段")

        if scene == "diagnostics":
            return self._parse_diagnostics(data)
        if scene == "voice":
            if "recognized_text" not in data:
                raise ValueError("voice 消息缺少 recognized_text")
            return [voice_text_update(data["recognized_text"], self.voice_config)]

        scene = SCENE_ALIASES.get(scene, scene)
        fields = SCENE_FIELDS.get(scene)
        if fields is None:
            raise ValueError(f"暂不支持的场景: {scene}；支持: {', '.join(SCENE_FIELDS)}")

        updates = [
            self._make_update(name, address, data[name], scale)
            for name, address, scale in fields
            if name in data
        ]
        if scene == "servo" and "direction" in data:
            text = data["direction"]
            if text not in ("正转", "反转", "停止"):
                raise ValueError("舵机方向文本必须是正转、反转或停止")
            updates.insert(0, {"name": "servo.direction", "address": addresses.SERVO_ROTATION_DIRECTION,
                               "data": text.encode("gb2312") + b"\xff\xff", "text": text})
        if not updates:
            raise ValueError(
                f"{scene} 消息中没有可更新的数据；可用字段: "
                f"{', '.join(name for name, _, _ in fields)}"
            )
        return updates

    def _parse_diagnostics(self, data):
        """状态结果写入已配置 VP；预留的 None 一律跳过。"""
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
                updates.append(self._make_update(f"diagnostics.{name}", address, addresses.STATUS_CODE_MAP[status]))
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
                updates.append(self._make_update(f"diagnostics.{field}", address, value))
        for item in updates:
            address = item["address"]
            if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
                raise ValueError("状态结果 VP 必须是 0~65535 的整数")
        return updates

    @staticmethod
    def _to_dict(message):
        """把 JSON 字符串或 dict 统一转换成 dict。"""

        if isinstance(message, dict):
            return message

        if isinstance(message, str):
            try:
                data = json.loads(message)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON 格式错误: {exc}") from exc

            if not isinstance(data, dict):
                raise ValueError("JSON 顶层必须是对象")

            return data

        raise TypeError("message 只支持 dict 或 JSON 字符串")

    @staticmethod
    def _make_update(name, address, value, scale=1):
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
