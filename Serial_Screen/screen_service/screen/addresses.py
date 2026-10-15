"""
串口屏变量地址表
用于统一管理 VisualBoard / JGUS 串口屏中的变量地址、键值返回地址和返回值。

原则：
1. 所有串口屏地址统一在本文件维护；
2. 业务场景代码中不要直接写十六进制地址；
3. 未确认的地址暂时使用 None，避免后续冲突；
4. “开始/停止”类按钮按下时，下发下面登记的键值返回地址和值；
5. 需要开发板执行实际动作的按钮，串口屏端应开启“下发串口”。
"""


# ============================================================
# 环境检测场景
# ============================================================

# 温度显示地址
ENV_TEMPERATURE = 0x1000

# 湿度显示地址
ENV_HUMIDITY = 0x1010

# 光照显示地址
ENV_LIGHT = 0x1020

# 开始 / 停止检测变量地址和返回值
ENV_DETECT_START_KEY_ADDR = 0x2000
ENV_DETECT_START_BACK_VALUE = 0x4000

ENV_DETECT_STOP_KEY_ADDR = 0x2010
ENV_DETECT_STOP_BACK_VALUE = 0x4010


# ============================================================
# 人体温度场景
# ============================================================

# 人体温度显示地址
BODY_TEMPERATURE = 0x1030

# 开始 / 停止检测变量地址和返回值
BODY_TEMP_DETECT_START_KEY_ADDR = 0x2030
BODY_TEMP_DETECT_START_BACK_VALUE = 0x4030

BODY_TEMP_DETECT_STOP_KEY_ADDR = 0x2040
BODY_TEMP_DETECT_STOP_BACK_VALUE = 0x4040


# ============================================================
# 超声波测距场景
# ============================================================

# 距离显示地址
ULTRASONIC_DISTANCE = 0x1040

# 开始 / 停止检测变量地址和返回值
ULTRASONIC_DETECT_START_KEY_ADDR = 0x2050
ULTRASONIC_DETECT_START_BACK_VALUE = 0x4050

ULTRASONIC_DETECT_STOP_KEY_ADDR  = 0x2060
ULTRASONIC_DETECT_STOP_BACK_VALUE = 0x4060


# ============================================================
# 烟雾报警场景
# ============================================================

# 烟雾数值显示地址
SMOKE_VALUE = 0x1050

# 开始 / 停止检测：键值返回
SMOKE_DETECT_START_KEY_ADDR = 0x2070
SMOKE_DETECT_START_BACK_VALUE = 0x4070

SMOKE_DETECT_STOP_KEY_ADDR  = 0x2080
SMOKE_DETECT_STOP_BACK_VALUE = 0x4080


# ============================================================
# 智能风扇场景
# ============================================================


# 启动 / 停止风扇：键值返回
FAN_SWITCH_START_KEY_ADDR = 0x2090
FAN_SWITCH_START_BACK_VALUE = 0x4090

FAN_SWITCH_STOP_KEY_ADDR  = 0x3000
FAN_SWITCH_STOP_BACK_VALUE = 0x5000


# ============================================================
# 智能门禁场景
# ============================================================
# 人脸录入：键值返回
FACE_ENROLL_START_KEY_ADDR = 0x3010
FACE_ENROLL_BACK_VALUE = 0x5010

# 启动 / 关闭门禁：键值返回
ACCESS_SWITCH_START_KEY_ADDR = 0x3020
ACCESS_START_BACK_VALUE = 0x5020


ACCESS_SWITCH_STOP_KEY_ADDR = 0x3030
ACCESS_SWITCH_STOP_BACK_VALUE = 0x5030

# “返回主页”只做串口屏内部页面跳转，不需要键值返回


# ============================================================
# 舵机控制
# ============================================================

# 舵机显示变量；往返范围由 Config.py 的 servo 配置。
SERVO_ROTATION_DIRECTION = 0x10F0  # GB2312 方向文本，3 Word；避开角度和语音缓冲区。
SERVO_TARGET_ANGLE = 0x1061  # 角度显示采用 10 倍缩放：900 -> 90°。

# 开始 / 停止变量地址和返回值
SERVO_CONTROL_START_KEY_ADDR = 0x3040
SERVO_CONTROL_START_BACK_VALUE = 0x5040

SERVO_CONTROL_STOP_KEY_ADDR = 0x3050
SERVO_CONTROL_STOP_BACK_VALUE = 0x5050


# ============================================================
# 步进电机控制
# ============================================================

# 步进电机控制变量
STEPPER_DIRECTION = 0x1062
STEPPER_NUMBER = 0x1063

# 开始 / 停止变量地址和返回值
STEPPER_CONTROL_START_KEY_ADDR = 0x3060
STEPPER_CONTROL_START_BACK_VALUE = 0x5060

STEPPER_CONTROL_STOP_KEY_ADDR = 0x3070
STEPPER_CONTROL_STOP_BACK_VALUE = 0x5070


# ============================================================
# 语音控制场景
# ============================================================
VOICE_RECOGNIZED_TEXT = 0x1064

VOICE_CONTROL_START_KEY_ADDR = 0x3080
VOICE_CONTROL_START_BACK_VALUE = 0x5080

VOICE_CONTROL_STOP_KEY_ADDR = 0x3090
VOICE_CONTROL_STOP_BACK_VALUE = 0x5090


# ============================================================
# 已确认地址映射
# 便于调试、日志打印或消息解析时按名称查找
# 这里只放已经确定的变量地址
# ============================================================

ADDRESS_MAP = {
    "environment.temperature": ENV_TEMPERATURE,
    "environment.humidity": ENV_HUMIDITY,
    "environment.light": ENV_LIGHT,
    "body.temperature": BODY_TEMPERATURE,
    "ultrasonic.distance": ULTRASONIC_DISTANCE,
    "smoke.value": SMOKE_VALUE,
    "servo.direction": SERVO_ROTATION_DIRECTION,
    "servo.target_angle": SERVO_TARGET_ANGLE,
    "stepper.direction": STEPPER_DIRECTION,
    "stepper.steps": STEPPER_NUMBER,
    "voice.recognized_text": VOICE_RECOGNIZED_TEXT,
}

# 步进参数输入控件需开启“下发串口”；舵机角度用于接收开发板的显示更新。
INPUT_EVENT_MAP = {
    STEPPER_DIRECTION: "stepper.direction",
    STEPPER_NUMBER: "stepper.steps",
}

# 串口屏键值回传转为事件名称。开发板业务程序收到事件后再执行实际动作。
BUTTON_EVENT_MAP = {
    (ENV_DETECT_START_KEY_ADDR, ENV_DETECT_START_BACK_VALUE): "environment.start",
    (ENV_DETECT_STOP_KEY_ADDR, ENV_DETECT_STOP_BACK_VALUE): "environment.stop",
    (BODY_TEMP_DETECT_START_KEY_ADDR, BODY_TEMP_DETECT_START_BACK_VALUE): "body_temperature.start",
    (BODY_TEMP_DETECT_STOP_KEY_ADDR, BODY_TEMP_DETECT_STOP_BACK_VALUE): "body_temperature.stop",
    (ULTRASONIC_DETECT_START_KEY_ADDR, ULTRASONIC_DETECT_START_BACK_VALUE): "ultrasonic.start",
    (ULTRASONIC_DETECT_STOP_KEY_ADDR, ULTRASONIC_DETECT_STOP_BACK_VALUE): "ultrasonic.stop",
    (SMOKE_DETECT_START_KEY_ADDR, SMOKE_DETECT_START_BACK_VALUE): "smoke.start",
    (SMOKE_DETECT_STOP_KEY_ADDR, SMOKE_DETECT_STOP_BACK_VALUE): "smoke.stop",
    (FAN_SWITCH_START_KEY_ADDR, FAN_SWITCH_START_BACK_VALUE): "fan.start",
    (FAN_SWITCH_STOP_KEY_ADDR, FAN_SWITCH_STOP_BACK_VALUE): "fan.stop",
    (FACE_ENROLL_START_KEY_ADDR, FACE_ENROLL_BACK_VALUE): "access.face_enroll",
    (ACCESS_SWITCH_START_KEY_ADDR, ACCESS_START_BACK_VALUE): "access.start",
    (ACCESS_SWITCH_STOP_KEY_ADDR, ACCESS_SWITCH_STOP_BACK_VALUE): "access.stop",
    (SERVO_CONTROL_START_KEY_ADDR, SERVO_CONTROL_START_BACK_VALUE): "servo.start",
    (SERVO_CONTROL_STOP_KEY_ADDR, SERVO_CONTROL_STOP_BACK_VALUE): "servo.stop",
    (STEPPER_CONTROL_START_KEY_ADDR, STEPPER_CONTROL_START_BACK_VALUE): "stepper.start",
    (STEPPER_CONTROL_STOP_KEY_ADDR, STEPPER_CONTROL_STOP_BACK_VALUE): "stepper.stop",
    (VOICE_CONTROL_START_KEY_ADDR, VOICE_CONTROL_START_BACK_VALUE): "voice.start",
    (VOICE_CONTROL_STOP_KEY_ADDR, VOICE_CONTROL_STOP_BACK_VALUE): "voice.stop",
}


# ============================================================
# 状态检测按钮与结果 VP
# 状态页只有三个检测按钮；每个设备不再单独配置检测按钮。
# 结果框初始留空，收到检测结果后写入下面的结果 VP。
# ============================================================

# 一键检测 / 只检测传感器 / 只检测执行器
ALL_STATUS_CHECK_KEY_ADDR = 0x9000
ALL_STATUS_CHECK_BACK_VALUE = 0x8000
SENSORS_STATUS_CHECK_KEY_ADDR = 0x9001
SENSORS_STATUS_CHECK_BACK_VALUE = 0x8001
ACTUATORS_STATUS_CHECK_KEY_ADDR = 0x9002
ACTUATORS_STATUS_CHECK_BACK_VALUE = 0x8002


# 温湿度传感器
DHT11_STATUS_RESULT_ADDR = 0x6000

# 光照传感器
LIGHT_STATUS_RESULT_ADDR = 0x6001

# 烟雾传感器
SMOKE_STATUS_RESULT_ADDR = 0x6002

# 人体温度传感器
BODY_TEMP_STATUS_RESULT_ADDR = 0x6003

# 超声波传感器
ULTRASONIC_STATUS_RESULT_ADDR = 0x6004

# 舵机
SERVO_STATUS_RESULT_ADDR = 0x6005

# 步进电机
STEPPER_STATUS_RESULT_ADDR = 0x6006

# 蜂鸣器
BUZZER_STATUS_RESULT_ADDR = 0x6007

# RGB 指示灯
RGB_STATUS_RESULT_ADDR = 0x6008

# 风扇
FAN_STATUS_RESULT_ADDR = 0x6009

# 电磁锁
LOCK_STATUS_RESULT_ADDR = 0x6010



# 检测进度和总结果地址尚未填写时保持 None，不会发送到地址 0。
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


def configured_status_button_events():
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


BUTTON_EVENT_MAP.update(configured_status_button_events())

def validate_status_result_addresses():
    """结果 VP 不得覆盖原业务变量或按钮 VP，也不得互相重叠。"""
    occupied = {address for address in ADDRESS_MAP.values()}
    occupied.update(address for address, _ in BUTTON_EVENT_MAP)
    for name, address in {**STATUS_RESULT_ADDRESS_MAP, **STATUS_META_ADDRESS_MAP}.items():
        if address is None:
            continue
        if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
            raise ValueError(f"{name} 的状态 VP 地址无效")
        if address in occupied:
            raise ValueError(f"{name} 的状态 VP 与已有变量、按钮或结果地址冲突")
        occupied.add(address)
    return True

validate_status_result_addresses()
