"""Shared diagnostics page: button and result VP definitions."""

from experiments.environment.serial_screen.addresses import DHT11_STATUS_RESULT_ADDR, LIGHT_STATUS_RESULT_ADDR
from experiments.smoke.serial_screen.addresses import SMOKE_STATUS_RESULT_ADDR
from experiments.body_temperature.serial_screen.addresses import BODY_TEMP_STATUS_RESULT_ADDR
from experiments.ultrasonic.serial_screen.addresses import ULTRASONIC_STATUS_RESULT_ADDR, BUZZER_STATUS_RESULT_ADDR
from experiments.servo.serial_screen.addresses import SERVO_STATUS_RESULT_ADDR
from experiments.stepper.serial_screen.addresses import STEPPER_STATUS_RESULT_ADDR
from experiments.fan.serial_screen.addresses import FAN_STATUS_RESULT_ADDR
from experiments.access.serial_screen.addresses import LOCK_STATUS_RESULT_ADDR
from hardware.rgb.status_addresses import RGB_STATUS_RESULT_ADDR

ALL_STATUS_CHECK_KEY_ADDR = 0x9000

ALL_STATUS_CHECK_BACK_VALUE = 0x8000

SENSORS_STATUS_CHECK_KEY_ADDR = 0x9001

SENSORS_STATUS_CHECK_BACK_VALUE = 0x8001

ACTUATORS_STATUS_CHECK_KEY_ADDR = 0x9002

ACTUATORS_STATUS_CHECK_BACK_VALUE = 0x8002























STATUS_CHECK_PROGRESS_ADDR = None

STATUS_CHECK_OVERALL_RESULT_ADDR = None

STATUS_CODE_MAP = {
    "unknown": 0,      # 尚未检测 / 暂无有效结论
    "normal": 1,       # 当前检测项通过
    "error": 2,        # 当前检测项失败
    "unconfigured": 3, # 型号、协议或反馈通道未配置
    "disabled": 4,     # 配置中未启用
    "checking": 5,     # 检测中
    "idle": 6,         # 执行器关闭，尚未检测运行反馈
}

STATUS_CHECK_BINDINGS = {
    "diagnostics.all": (ALL_STATUS_CHECK_KEY_ADDR, ALL_STATUS_CHECK_BACK_VALUE),
    "diagnostics.sensors": (SENSORS_STATUS_CHECK_KEY_ADDR, SENSORS_STATUS_CHECK_BACK_VALUE),
    "diagnostics.actuators": (ACTUATORS_STATUS_CHECK_KEY_ADDR, ACTUATORS_STATUS_CHECK_BACK_VALUE),
}

STATUS_RESULT_ADDRESS_MAP = {
    "dht11": DHT11_STATUS_RESULT_ADDR,
    "bh1750": LIGHT_STATUS_RESULT_ADDR,
    "smoke": SMOKE_STATUS_RESULT_ADDR,
    "body_temperature": BODY_TEMP_STATUS_RESULT_ADDR,
    "ultrasonic": ULTRASONIC_STATUS_RESULT_ADDR,
    "servo": SERVO_STATUS_RESULT_ADDR,
    "stepper": STEPPER_STATUS_RESULT_ADDR,
    "buzzer": BUZZER_STATUS_RESULT_ADDR,
    "rgb": RGB_STATUS_RESULT_ADDR,
    "fan": FAN_STATUS_RESULT_ADDR,
    "lock": LOCK_STATUS_RESULT_ADDR,
}

STATUS_META_ADDRESS_MAP = {
    # busy 只用于程序内部判断，当前状态页没有对应显示控件。
    "busy": None,
    "progress": STATUS_CHECK_PROGRESS_ADDR,
    "overall": STATUS_CHECK_OVERALL_RESULT_ADDR,
}
