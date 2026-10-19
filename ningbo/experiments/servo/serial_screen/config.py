"""Configuration owned by experiments/servo/serial_screen."""
from dataclasses import dataclass, field
import math

@dataclass(frozen=True)
class ScreenControlConfig:
    # 方向选择控件必须使用与这里一致的 Word 值。
    forward_value: int = 0
    reverse_value: int = 1


def validate(self):
    directions = (self.screen_control.forward_value, self.screen_control.reverse_value)
    if (any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF for value in directions)
            or directions[0] == directions[1]):
        raise ValueError("屏幕方向值必须为两个不同的 16 位 Word")
