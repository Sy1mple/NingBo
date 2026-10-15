"""宁波设备唯一配置入口。BOARD 是 Jetson 40 针排针的物理编号。

接线按现场确认结果维护；设备配置与业务阈值统一修改本文件。
Config 可以作为配置类使用；DEFAULT_CONFIG 是服务使用的默认实例。
"""
from dataclasses import dataclass, field
import math
from pathlib import Path


@dataclass(frozen=True)
class Dht11Config:
    enabled: bool = True
    pin: int = 37  # 已在 Xavier NX 实机确认数据线位于 BOARD 37。
    backend: str = "gpiod_native"
    native_helper: str = "JETSON/Sensor/native/dht11_gpiod"
    gpio_chip: str = "/dev/gpiochip1"
    line_offset: int = 151  # BOARD 37 / PY.02 在当前 Xavier NX 系统上的 GPIO line。
    native_timeout_seconds: float = 0.5
    interval: float = 2.0
    attempts: int = 5
    retry_interval: float = 1.1
    start_low_seconds: float = 0.020
    edge_timeout_seconds: float = 0.005
    one_bit_threshold_us: float = 50.0
    temperature_range: tuple = (0.0, 60.0)
    humidity_range: tuple = (0.0, 100.0)


@dataclass(frozen=True)
class LightConfig:
    enabled: bool = True
    address: int = 0x23
    interval: float = 1.0
    measurement_seconds: float = 0.180
    counts_per_lux: float = 1.2
    display_full_scale_lux: float = 1000.0  # 沿用合并包的示例百分比换算


@dataclass(frozen=True)
class UltrasonicConfig:
    enabled: bool = True
    near_buzzer_enabled: bool = True  # 距离小于 rgb_feedback.distance_danger_cm 时鸣响。
    trig_pin: int = 32
    echo_pin: int = 33
    interval: float = 1.0
    settle_seconds: float = 0.000010
    trigger_seconds: float = 0.000012
    rise_timeout_seconds: float = 0.030
    fall_timeout_seconds: float = 0.040
    minimum_pulse_us: float = 100.0
    maximum_distance_cm: float = 400.0
    sound_speed_cm_per_second: float = 34300.0


@dataclass(frozen=True)
class SmokeConfig:
    enabled: bool = True
    port: str = "/dev/ttyTHS0"  # Xavier NX：BOARD 8=TX、10=RX。
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    timeout: float = 0.25
    interval: float = 0.2
    read_size: int = 256
    protocol: str = "ffaa"  # FF AA 浓度高字节 浓度低字节 3C；单位待型号确认。
    stale_after_seconds: float = 5.0


@dataclass(frozen=True)
class BodyTemperatureConfig:
    enabled: bool = True
    model: str = "mlx90614"  # GY-906，与 BH1750 共用 I2C8。
    address: int = 0x5A
    interval: float = 1.0
    temperature_range: tuple = (-70.0, 380.0)
    ambient_range: tuple = (-40.0, 125.0)
    display_range: tuple = (0.0, 100.0)  # 屏幕显示范围；RGB 的人体温度范围单独判断。


@dataclass(frozen=True)
class SwitchConfig:
    pin: int
    active_high: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class RgbConfig:
    pins: tuple = (12, 35, 38)  # 红、绿、蓝；用户确认的 BOARD 物理引脚。
    active_high: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class ServoConfig:
    pin: int = 15
    enabled: bool = True
    min_angle: float = 0.0
    max_angle: float = 180.0
    default_angle: float = 90.0  # 未启动时的内部初始值；屏幕启动使用下方往返范围。
    minimum_pulse_us: float = 500.0
    maximum_pulse_us: float = 2500.0
    frequency_hz: float = 50.0
    # 硬件 PWM：20 ms 周期；45°/90°/135°对应 1000/1500/2000 us。
    sweep_min_angle: float = 45.0
    sweep_max_angle: float = 135.0
    sweep_interval_seconds: float = 4.0  # 每个位置保持 4 秒。
    display_interval_seconds: float = 0.2


@dataclass(frozen=True)
class StepperConfig:
    pins: tuple = (13, 16, 18, 22)
    enabled: bool = True
    sequence: tuple = (
        (1, 0, 1, 0), (1, 0, 0, 0), (1, 1, 0, 0), (0, 1, 0, 0),
        (0, 1, 1, 0), (0, 0, 1, 0), (0, 0, 1, 1), (1, 0, 0, 1),
    )
    default_speed_ms: int = 8
    minimum_speed_ms: int = 2
    maximum_speed_ms: int = 20
    maximum_steps: int = 100000
    idle_wait: float = 0.02


@dataclass(frozen=True)
class OledConfig:
    enabled: bool = True
    address: int = 0x3C
    driver: str = "sh1106"  # 合并包默认；原独立程序为 SSD1306，按实际模块修改
    width: int = 128
    height: int = 64
    interval: float = 2.0
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_size: int = 14
    line_height: int = 16


@dataclass(frozen=True)
class CurrentChannel:
    address: int
    channel: int
    shunt_ohms: float
    threshold_ma: float = 30.0  # 示例阈值，各设备安装后分别标定
    max_current_ma: float = None
    off_current_ma: float = None


@dataclass(frozen=True)
class ActuatorDiagnosticConfig:
    sample_count: int = 3
    sample_interval: float = 0.02
    gpio_readback: bool = True


@dataclass(frozen=True)
class RgbFeedbackConfig:
    enabled: bool = True
    # 教学演示规则；型号/测温部位确认后按真实模块标定。
    body_temperature_high_c: float = 37.5
    body_temperature_valid_range: tuple = (35.0, 45.0)
    distance_danger_cm: float = 5.0    # 红灯 + 蜂鸣
    distance_caution_cm: float = 8.0   # 蓝 / 绿分界；5≤d<8 蓝，d≥8 绿
    sample_max_age_seconds: float = 5.0
    waiting_color: str = "blue"


@dataclass(frozen=True)
class ScreenControlConfig:
    # 方向选择控件必须使用与这里一致的 Word 值。
    forward_value: int = 0
    reverse_value: int = 1


def _voice_model_path():
    return str(Path(__file__).resolve().parents[1] / "Voice" / "models" / "whisper-tiny.pt")


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = True
    backend: str = "xfyun"
    cuda_device: int = 0
    language: str = "zh"
    silence_rms: float = 0.015
    input_gain: float = 30.0  # AIMIC-M4 实测输入较小；分句及识别前放大并限幅。
    input_latency_seconds: float = 0.5  # 为 GPU 推理期间的采音回调保留缓冲。
    silence_seconds: float = 0.6
    minimum_utterance_seconds: float = 0.5
    maximum_utterance_seconds: float = 8.0
    no_speech_threshold: float = 0.6
    input_device: object = "auto"  # 自动扫描 USB 输入，优先 AIMIC-M4/AISPEECH，不依赖声卡编号。
    sample_rate: int = None  # None 使用麦克风支持的默认采样率。
    block_duration_ms: int = 40
    audio_queue_blocks: int = 128
    result_queue_size: int = 8
    read_timeout: float = 0.1
    partial_interval: float = 0.3
    show_partial: bool = True
    xfyun_endpoint: str = "wss://iat-api.xfyun.cn/v2/iat"
    xfyun_credentials_file: str = field(default_factory=lambda: str(Path(__file__).resolve().parents[2] / ".env.voice"))
    xfyun_usage_file: str = field(default_factory=lambda: str(Path(__file__).resolve().parents[2] / "data" / "voice_usage.sqlite3"))
    xfyun_daily_limit: int = 450  # 本项目保守上限；平台默认免费额度为每日 500 次。
    xfyun_connect_timeout: float = 3.0
    xfyun_final_timeout: float = 2.0
    xfyun_trigger_seconds: float = 0.12
    xfyun_max_seconds: float = 50.0  # 每个会话小于平台 60 秒限制。
    model_path: str = field(default_factory=_voice_model_path)
    text_encoding: str = "gb2312"  # 与实机文本控件的 0x01 GB2312 内码一致。
    text_words: int = 32       # 64 Byte，含字符串结束标记；屏幕控件长度设置为 64 Byte。


@dataclass(frozen=True)
class CameraConfig:
    device: object = "auto"  # 扫描 USB 图像采集节点；可用 --camera-device 显式指定。
    width: int = 640
    height: int = 480
    fps: int = 15


def _face_model_path(filename):
    return str(Path(__file__).resolve().parents[1] / "Vision" / "models" / filename)


def _face_database_path():
    return str(Path(__file__).resolve().parents[2] / "data" / "faces.sqlite3")


@dataclass(frozen=True)
class FaceConfig:
    enabled: bool = True
    backend: str = "tensorrt"
    cuda_device: int = 0
    precision: str = "fp16"
    dla_core: int = None  # None 使用 GPU；0/1 使用 DLA，须在 Jetson 上构建引擎。
    dla_gpu_fallback: bool = True  # DLA 不支持的层交给 GPU，绝不交给 CPU。
    engine_cache: str = field(default_factory=lambda: str(Path(__file__).resolve().parents[2] / "data" / "engines"))
    workspace_mb: int = 256
    detector_path: str = field(default_factory=lambda: _face_model_path("face_detection_yunet_2023mar.onnx"))
    recognizer_path: str = field(default_factory=lambda: _face_model_path("face_recognition_sface_2021dec.onnx"))
    database_path: str = field(default_factory=_face_database_path)
    detector_score: float = 0.85
    detector_nms: float = 0.3
    detector_top_k: int = 5000
    detector_input_size: int = 640  # 官方 YuNet ONNX 的固定输入，不能任意改成 320。
    inference_width: int = 640
    feature_dimension: int = 128
    similarity_threshold: float = 0.363  # OpenCV 官方示例分界，部署时用真实摄像头标定。
    ambiguity_margin: float = 0.05
    minimum_face_pixels: int = 80
    minimum_sharpness: float = 50.0
    processing_interval: float = 0.25
    consecutive_matches: int = 3
    enroll_samples: int = 5
    enroll_interval: float = 0.3
    enroll_timeout: float = 20.0
    enroll_consistency: float = 0.6
    duplicate_threshold: float = 0.6
    enroll_name: str = ""  # 未填时自动生成人员编号。
    enroll_indicator_seconds: float = 2.0
    result_queue_size: int = 16
    # 先核对“通电开锁”的继电器含义；确认后启用自动开锁。
    unlock_on_match: bool = False
    unlock_seconds: float = 3.0


def _current_channels():
    return {
        "lock": CurrentChannel(0x48, 0, 1.0),
        "fan": CurrentChannel(0x48, 1, 1.0),
        "servo": CurrentChannel(0x48, 2, 1.0),
        "stepper": CurrentChannel(0x48, 3, 0.5),
        "rgb": CurrentChannel(0x49, 0, 10.0),
        "buzzer": CurrentChannel(0x49, 1, 22.0),
        "bus5v": CurrentChannel(0x49, 2, 0.1),
    }


@dataclass(frozen=True)
class Ads1115Config:
    # 现场已在 Jetson I2C8 探测到 0x48。量程不是输入耐压。
    enabled: bool = True
    address: int = 0x48
    full_scale_volts: float = 4.096
    timeout_seconds: float = 0.2
    feedback_channels: dict = field(default_factory=lambda: {"servo": 0, "lock": 1, "fan": 2, "rgb": 3, "buzzer": 0, "stepper": 1})
    # 用户确认：0x48 A0舵机/A1锁/A2风扇/A3指示灯；0x49 A0蜂鸣器/A1步进；ADDR 接线需进一步核实。
    feedback_addresses: dict = field(default_factory=lambda: {"buzzer": 0x49, "stepper": 0x49})
    # 2026-10-14 实测：PWM 关闭约 8.6mV，位置保持约 13mV。
    # (关闭电压上限, 运行电压下限)，中间区间不作确定判断。
    feedback_voltage_limits: dict = field(default_factory=lambda: {"servo": (0.0100, 0.0115)})


@dataclass(frozen=True)
class CurrentConfig:
    # 图片无法确认 ADS1115 与分流电阻，默认关闭；确认接线和量程后开启
    enabled: bool = False
    conversion_seconds: float = 0.010
    full_scale_volts: float = 2.048  # 必须与驱动中的 PGA 配置匹配
    channels: dict = field(default_factory=_current_channels)


@dataclass(frozen=True)
class ScreenConfig:
    port: str = "auto"  # 扫描 USB 串口并验证屏幕协议应答，避免编号/别名变化。
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    read_timeout: float = 0.5
    write_timeout: float = 1.0
    refresh_interval: float = 1.0
    event_poll_interval: float = 0.05
    auto_probe_timeout: float = 0.35
    auto_probe_attempts: int = 2


@dataclass(frozen=True)
class Config:
    gpio_numbering: str = "BOARD"
    i2c_bus: int = 8
    i2c_pins: tuple = (3, 5)
    uart_pins: tuple = (8, 10)
    thread_join_timeout: float = 5.0
    dht11: Dht11Config = field(default_factory=Dht11Config)
    light: LightConfig = field(default_factory=LightConfig)
    ultrasonic: UltrasonicConfig = field(default_factory=UltrasonicConfig)
    smoke: SmokeConfig = field(default_factory=SmokeConfig)
    body_temperature: BodyTemperatureConfig = field(default_factory=BodyTemperatureConfig)
    servo: ServoConfig = field(default_factory=ServoConfig)
    stepper: StepperConfig = field(default_factory=StepperConfig)
    buzzer: SwitchConfig = field(default_factory=lambda: SwitchConfig(40, active_high=False))
    lock: SwitchConfig = field(default_factory=lambda: SwitchConfig(29))
    fan: SwitchConfig = field(default_factory=lambda: SwitchConfig(31))
    rgb: RgbConfig = field(default_factory=RgbConfig)
    oled: OledConfig = field(default_factory=OledConfig)
    current: CurrentConfig = field(default_factory=CurrentConfig)
    ads1115: Ads1115Config = field(default_factory=Ads1115Config)
    actuator_diagnostics: ActuatorDiagnosticConfig = field(default_factory=ActuatorDiagnosticConfig)
    rgb_feedback: RgbFeedbackConfig = field(default_factory=RgbFeedbackConfig)
    screen_control: ScreenControlConfig = field(default_factory=ScreenControlConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    face: FaceConfig = field(default_factory=FaceConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)

    def gpio_devices(self):
        devices = {}
        for name in ("buzzer", "lock", "fan", "servo"):
            device = getattr(self, name)
            if device.enabled:
                devices[device.pin] = name
        if self.dht11.enabled:
            devices[self.dht11.pin] = "dht11"
        if self.ultrasonic.enabled:
            devices[self.ultrasonic.trig_pin] = "ultrasonic.trig"
            devices[self.ultrasonic.echo_pin] = "ultrasonic.echo"
        for name in ("rgb", "stepper"):
            device = getattr(self, name)
            if device.enabled:
                for index, pin in enumerate(device.pins):
                    devices[pin] = f"{name}.{index}"
        return devices

    def validate(self):
        if self.gpio_numbering != "BOARD":
            raise ValueError("当前接线配置只支持 BOARD 物理编号")
        pins = []
        for name in ("buzzer", "lock", "fan", "servo", "dht11"):
            device = getattr(self, name)
            if device.enabled:
                pins.append(device.pin)
        if self.ultrasonic.enabled:
            pins += [self.ultrasonic.trig_pin, self.ultrasonic.echo_pin]
        for name in ("rgb", "stepper"):
            device = getattr(self, name)
            if device.enabled:
                pins += list(device.pins)
        allowed = {7, 11, 12, 13, 15, 16, 18, 19, 21, 22, 23, 24, 26, 29, 31, 32, 33, 35, 36, 37, 38, 40}
        if len(pins) != len(set(pins)):
            raise ValueError("Config.py 中存在 GPIO 引脚冲突")
        if set(pins) - allowed or set(pins) & (set(self.i2c_pins) | set(self.uart_pins)):
            raise ValueError("GPIO 配置占用了电源/地/总线或无效引脚")
        if not isinstance(self.screen.port, str) or not self.screen.port.strip() or self.screen.baudrate <= 0:
            raise ValueError("串口屏端口或波特率无效")
        if (isinstance(self.screen.auto_probe_timeout, bool) or not math.isfinite(self.screen.auto_probe_timeout) or not 0.05 <= self.screen.auto_probe_timeout <= 2
                or isinstance(self.screen.auto_probe_attempts, bool) or not isinstance(self.screen.auto_probe_attempts, int)
                or not 1 <= self.screen.auto_probe_attempts <= 3):
            raise ValueError("串口屏自动探测超时或重试次数无效")
        if self.smoke.enabled and self.screen.port == self.smoke.port:
            raise ValueError("串口屏和烟雾传感器不能共用同一个串口")
        if (self.screen.bytesize, self.screen.parity, self.screen.stopbits) != (8, "N", 1):
            raise ValueError("JGUS 串口屏当前使用 8N1")
        if self.i2c_bus < 0:
            raise ValueError("I2C 总线号必须非负")
        for name in ("dht11", "light", "ultrasonic", "smoke", "body_temperature", "oled"):
            value = getattr(self, name).interval
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 采集间隔必须为正数")
        if self.body_temperature.enabled and (not self.body_temperature.model or self.body_temperature.model == "unconfigured"):
            raise ValueError("启用人体温度前需填写 mlx90614 型号，或为 DeviceService 注入真实驱动")
        body = self.body_temperature
        if isinstance(body.address, bool) or not isinstance(body.address, int) or not 0x08 <= body.address <= 0x77:
            raise ValueError("人体温度 I2C 地址必须是有效的 7 位地址")
        for label, limits in (("测量", body.temperature_range), ("环境", body.ambient_range), ("显示", body.display_range)):
            if (len(limits) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in limits)
                    or not limits[0] < limits[1]):
                raise ValueError(f"人体温度{label}范围无效")
        if not 0 <= body.display_range[0] < body.display_range[1] <= 327.67:
            raise ValueError("人体温度显示范围超出屏幕 Word 范围")
        if self.smoke.protocol not in ("raw", "ffaa"):
            raise ValueError("烟雾协议必须为 raw 或 ffaa")
        if self.smoke.protocol == "ffaa" and (self.smoke.bytesize, self.smoke.parity, self.smoke.stopbits) != (8, "N", 1):
            raise ValueError("FFAA 烟雾协议使用 8N1")
        if (isinstance(self.smoke.read_size, bool) or not isinstance(self.smoke.read_size, int)
                or not 5 <= self.smoke.read_size <= 4096
                or not math.isfinite(self.smoke.stale_after_seconds) or self.smoke.stale_after_seconds <= 0):
            raise ValueError("烟雾读缓冲或有效数据超时无效")
        if self.smoke.baudrate <= 0 or not self.smoke.port or self.smoke.read_size <= 0 or self.smoke.timeout <= 0:
            raise ValueError("烟雾串口、波特率、读缓冲或超时无效")
        if self.smoke.bytesize not in (5, 6, 7, 8) or self.smoke.parity not in ("N", "E", "O", "M", "S") or self.smoke.stopbits not in (1, 1.5, 2):
            raise ValueError("烟雾串口数据格式无效")
        timings = (self.screen.read_timeout, self.screen.write_timeout, self.screen.refresh_interval,
                   self.screen.event_poll_interval, self.thread_join_timeout, self.dht11.start_low_seconds,
                   self.dht11.edge_timeout_seconds, self.dht11.native_timeout_seconds,
                   self.light.measurement_seconds, self.light.counts_per_lux, self.light.display_full_scale_lux,
                   self.ultrasonic.settle_seconds, self.ultrasonic.trigger_seconds,
                   self.ultrasonic.rise_timeout_seconds, self.ultrasonic.fall_timeout_seconds,
                   self.ultrasonic.sound_speed_cm_per_second, self.current.conversion_seconds,
                   self.servo.frequency_hz, self.stepper.idle_wait)
        if any(not math.isfinite(value) or value <= 0 for value in timings):
            raise ValueError("时序、频率与比例配置必须是有限正数")
        if self.dht11.interval < 1 or self.dht11.retry_interval < 1 or self.dht11.attempts < 1:
            raise ValueError("DHT11 读取/重试间隔至少 1 秒且尝试次数至少 1")
        if self.dht11.backend not in ("gpiod_native", "python_poll"):
            raise ValueError("DHT11 backend 仅支持 gpiod_native 或 python_poll")
        if not isinstance(self.ultrasonic.near_buzzer_enabled, bool):
            raise ValueError("超声波 near_buzzer_enabled 必须是布尔值")
        if (not self.dht11.native_helper or not self.dht11.gpio_chip.startswith("/dev/gpiochip")
                or isinstance(self.dht11.line_offset, bool) or self.dht11.line_offset < 0):
            raise ValueError("DHT11 原生采样器路径、gpiochip 或 line offset 无效")
        if not 0 <= self.servo.min_angle < self.servo.max_angle:
            raise ValueError("舵机角度范围无效")
        if not self.servo.min_angle <= self.servo.default_angle <= self.servo.max_angle:
            raise ValueError("舵机默认角度超出范围")
        if self.servo.frequency_hz <= 0 or not 0 < self.servo.minimum_pulse_us < self.servo.maximum_pulse_us < 1e6 / self.servo.frequency_hz:
            raise ValueError("舵机脉宽或频率配置无效")
        sweep = self.servo
        if (any(isinstance(v, bool) or not math.isfinite(v) for v in
                (sweep.sweep_min_angle, sweep.sweep_max_angle,
                 sweep.sweep_interval_seconds, sweep.display_interval_seconds))
                or not sweep.min_angle <= sweep.sweep_min_angle < sweep.sweep_max_angle <= sweep.max_angle
                or sweep.sweep_interval_seconds <= 0 or sweep.display_interval_seconds <= 0):
            raise ValueError("舵机循环范围、停留时间或刷新周期无效")
        if self.oled.driver not in ("sh1106", "ssd1306"):
            raise ValueError("OLED 仅支持 sh1106 或 ssd1306")
        if not 1 <= self.oled.width <= 128 or self.oled.height not in (32, 64):
            raise ValueError("OLED 当前支持宽度 1~128、高度 32 或 64")
        if len(self.rgb.pins) != 3 or len(self.stepper.pins) != 4:
            raise ValueError("RGB/步进电机引脚数量无效")
        if not self.stepper.sequence or any(len(row) != 4 or any(x not in (0, 1) for x in row) for row in self.stepper.sequence):
            raise ValueError("步进电机相序必须由四路 0/1 电平组成")
        if not 0 < self.stepper.minimum_speed_ms <= self.stepper.default_speed_ms <= self.stepper.maximum_speed_ms:
            raise ValueError("步进速度范围无效")
        addresses = [device.address for device in (self.light, self.oled) if device.enabled]
        if self.current.enabled:
            addresses += sorted({ch.address for ch in self.current.channels.values()})
        if len(addresses) != len(set(addresses)) or any(not 0x08 <= addr <= 0x77 for addr in addresses):
            raise ValueError("I2C 地址冲突或不在 7 位设备地址范围")
        if self.ads1115.address not in range(0x48, 0x4c) or self.ads1115.full_scale_volts not in (6.144, 4.096, 2.048, 1.024, 0.512, 0.256):
            raise ValueError("ADS1115 地址或电压量程无效")
        if not math.isfinite(self.ads1115.timeout_seconds) or not 0 < self.ads1115.timeout_seconds <= 2:
            raise ValueError("ADS1115 超时必须大于 0 且不超过 2 秒")
        if any(name not in ("servo", "stepper", "buzzer", "rgb", "fan", "lock")
               or isinstance(channel, bool) or channel not in range(4)
               for name, channel in self.ads1115.feedback_channels.items()):
            raise ValueError("ADS1115 反馈设备或通道无效")
        if any(name not in self.ads1115.feedback_channels or address not in range(0x48, 0x4c)
               for name, address in self.ads1115.feedback_addresses.items()):
            raise ValueError("ADS1115 反馈地址无效")
        for name, limits in self.ads1115.feedback_voltage_limits.items():
            if (name not in self.ads1115.feedback_channels or len(limits) != 2
                    or not all(math.isfinite(v) for v in limits)
                    or not 0 <= limits[0] < limits[1] < self.ads1115.full_scale_volts):
                raise ValueError("ADS1115 反馈电压阈值无效")
        if self.ads1115.enabled and self.ads1115.address in {d.address for d in (self.light, self.oled) if d.enabled}:
            raise ValueError("ADS1115 与其他 I2C 设备地址冲突")
        if self.current.full_scale_volts != 2.048:
            raise ValueError("ADS1115 当前驱动使用 ±2.048V PGA，full_scale_volts 必须匹配")
        locations = set()
        for name, channel in self.current.channels.items():
            if channel.channel not in range(4) or channel.shunt_ohms <= 0 or channel.threshold_ma < 0:
                raise ValueError(f"{name} 电流通道/分流电阻/阈值无效")
            location = (channel.address, channel.channel)
            if location in locations:
                raise ValueError("ADS1115 采样通道重复")
            locations.add(location)
            if not math.isfinite(channel.shunt_ohms) or not math.isfinite(channel.threshold_ma):
                raise ValueError(f"{name} 电流电阻/阈值必须有限")
            if channel.max_current_ma is not None and (not math.isfinite(channel.max_current_ma)
                                                      or channel.max_current_ma <= channel.threshold_ma):
                raise ValueError(f"{name} 运行电流上限必须大于运行下限")
            if channel.off_current_ma is not None and (not math.isfinite(channel.off_current_ma)
                                                      or not 0 < channel.off_current_ma <= channel.threshold_ma):
                raise ValueError(f"{name} 关闭电流上限必须为正且不大于运行下限")
        policy = self.actuator_diagnostics
        if isinstance(policy.sample_count, bool) or not isinstance(policy.sample_count, int) or not 1 <= policy.sample_count <= 31:
            raise ValueError("执行器电流采样次数必须为 1~31 的整数")
        if not math.isfinite(policy.sample_interval) or policy.sample_interval < 0:
            raise ValueError("执行器电流采样间隔必须是有限非负数")
        if policy.sample_count * (policy.sample_interval + self.current.conversion_seconds) >= self.thread_join_timeout:
            raise ValueError("执行器电流采样耗时不能超过线程关闭超时")
        feedback = self.rgb_feedback
        low, high = feedback.body_temperature_valid_range
        if (any(not math.isfinite(value) for value in (low, high, feedback.body_temperature_high_c))
                or not low <= feedback.body_temperature_high_c < high):
            raise ValueError("RGB 体温有效范围和高温阈值无效")
        if (not math.isfinite(feedback.distance_danger_cm) or not math.isfinite(feedback.distance_caution_cm)
                or not 0 < feedback.distance_danger_cm < feedback.distance_caution_cm
                or feedback.distance_caution_cm > self.ultrasonic.maximum_distance_cm):
            raise ValueError("RGB 距离阈值须满足 0 < danger < caution ≤ 超声波最大距离")
        if not math.isfinite(feedback.sample_max_age_seconds) or feedback.sample_max_age_seconds <= 0:
            raise ValueError("RGB 读数有效期必须为有限正数")
        if feedback.waiting_color not in ("blue", "off"):
            raise ValueError("RGB 等待颜色只能为 blue 或 off")
        directions = (self.screen_control.forward_value, self.screen_control.reverse_value)
        if (any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF for value in directions)
                or directions[0] == directions[1]):
            raise ValueError("屏幕方向值必须为两个不同的 16 位 Word")
        voice = self.voice
        if voice.backend not in ("xfyun", "whisper_cuda", "vosk"):
            raise ValueError("语音后端须为 xfyun、whisper_cuda 或 vosk")
        if voice.xfyun_endpoint != "wss://iat-api.xfyun.cn/v2/iat":
            raise ValueError("讯飞接口须使用已核对的官方 WSS 地址")
        if isinstance(voice.xfyun_daily_limit, bool) or not isinstance(voice.xfyun_daily_limit, int) or not 1 <= voice.xfyun_daily_limit <= 450:
            raise ValueError("讯飞本项目每日调用上限须为 1~450 次")
        if (not voice.xfyun_credentials_file or not voice.xfyun_usage_file
                or any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in
                       (voice.xfyun_connect_timeout, voice.xfyun_final_timeout, voice.xfyun_trigger_seconds, voice.xfyun_max_seconds))
                or voice.xfyun_connect_timeout >= self.thread_join_timeout
                or voice.xfyun_final_timeout >= self.thread_join_timeout
                or not voice.xfyun_trigger_seconds < voice.xfyun_max_seconds <= 50):
            raise ValueError("讯飞配置路径、超时或会话长度无效")
        if isinstance(voice.cuda_device, bool) or not isinstance(voice.cuda_device, int) or voice.cuda_device < 0:
            raise ValueError("voice.cuda_device 必须为非负整数")
        if not voice.language or not isinstance(voice.language, str):
            raise ValueError("语音语言不能为空")
        if not math.isfinite(voice.silence_rms) or not 0 < voice.silence_rms < 1:
            raise ValueError("语音静音 RMS 阈值须在 0~1 之间")
        if isinstance(voice.input_latency_seconds, bool) or not math.isfinite(voice.input_latency_seconds) or not 0 < voice.input_latency_seconds <= 2:
            raise ValueError("语音采音缓冲时长须为 0~2 秒之间的有限正数")
        if isinstance(voice.input_gain, bool) or not math.isfinite(voice.input_gain) or not 0 < voice.input_gain <= 100:
            raise ValueError("语音输入增益须为 0~100 之间的有限正数")
        if any(not math.isfinite(value) or value <= 0 for value in
               (voice.silence_seconds, voice.minimum_utterance_seconds, voice.maximum_utterance_seconds)):
            raise ValueError("语音分句时长必须是有限正数")
        if not voice.minimum_utterance_seconds < voice.maximum_utterance_seconds <= 25:
            raise ValueError("语音最短分句须小于最长分句且最长不超过 25 秒")
        if not 0 < voice.no_speech_threshold < 1:
            raise ValueError("语音无讲话概率阈值须在 0~1 之间")
        if (isinstance(voice.input_device, bool) or not isinstance(voice.input_device, (int, str))
                or (isinstance(voice.input_device, int) and voice.input_device < 0)
                or (isinstance(voice.input_device, str) and not voice.input_device.strip())):
            raise ValueError("USB 麦克风设备须为 auto、非负编号或名称关键字")
        if voice.sample_rate is not None and (isinstance(voice.sample_rate, bool) or not isinstance(voice.sample_rate, int)
                                              or not 8000 <= voice.sample_rate <= 192000):
            raise ValueError("语音采样率须为 8000~192000 的整数或 None")
        for name, low, high in (("block_duration_ms", 10, 1000), ("audio_queue_blocks", 1, 256),
                                ("result_queue_size", 1, 256), ("text_words", 2, 126)):
            value = getattr(voice, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"voice.{name} 必须为 {low}~{high} 的整数")
        if voice.text_encoding not in ("gbk", "gb2312", "utf-16-be"):
            raise ValueError("屏幕语音文本编码须为 gbk、gb2312 或 utf-16-be")
        if not voice.model_path:
            raise ValueError("语音模型目录不能为空")
        if any(not math.isfinite(value) or value <= 0 for value in (voice.read_timeout, voice.partial_interval)):
            raise ValueError("语音读取超时与临时结果间隔必须为有限正数")
        if voice.read_timeout >= self.thread_join_timeout:
            raise ValueError("语音读取超时必须小于线程关闭超时")
        camera = self.camera
        if (isinstance(camera.device, bool) or not isinstance(camera.device, (int, str))
                or (isinstance(camera.device, int) and camera.device < 0)
                or (isinstance(camera.device, str) and (not camera.device.strip() or "://" in camera.device))):
            raise ValueError("USB 摄像头须为 auto、非负编号或本地设备路径")
        for name in ("width", "height", "fps"):
            value = getattr(camera, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"camera.{name} 必须是正整数")
        face = self.face
        if face.backend not in ("tensorrt", "opencv_cpu"):
            raise ValueError("人脸后端须为 tensorrt 或显式选择的 opencv_cpu 调试后端")
        if isinstance(face.cuda_device, bool) or not isinstance(face.cuda_device, int) or face.cuda_device < 0:
            raise ValueError("face.cuda_device 必须为非负整数")
        if face.precision not in ("fp16", "fp32"):
            raise ValueError("TensorRT 精度须为 fp16 或 fp32")
        if face.dla_core is not None and (isinstance(face.dla_core, bool) or face.dla_core not in (0, 1)):
            raise ValueError("Xavier NX 的 DLA 核编号须为 0、1 或 None")
        if face.dla_core is not None and (face.precision != "fp16" or face.backend != "tensorrt"):
            raise ValueError("DLA 当前需使用 TensorRT FP16")
        if not isinstance(face.dla_gpu_fallback, bool) or not face.engine_cache:
            raise ValueError("DLA GPU 兼容配置或引擎缓存路径无效")
        if isinstance(face.workspace_mb, bool) or not isinstance(face.workspace_mb, int) or face.workspace_mb < 16:
            raise ValueError("TensorRT 工作区须为至少 16 MB 的整数")
        if not isinstance(face.enabled, bool) or not isinstance(face.unlock_on_match, bool):
            raise ValueError("人脸启用和自动开锁配置必须为布尔值")
        if not all((face.detector_path, face.recognizer_path, face.database_path)):
            raise ValueError("人脸模型和人脸库路径不能为空")
        for name in ("detector_top_k", "detector_input_size", "inference_width", "minimum_face_pixels", "consecutive_matches",
                     "enroll_samples", "result_queue_size"):
            value = getattr(face, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"face.{name} 必须是正整数")
        if face.detector_input_size % 32 or face.inference_width > face.detector_input_size:
            raise ValueError("人脸引擎输入须为 32 的倍数且不小于缩放宽度，当前模型原生输入为 640")
        if face.feature_dimension != 128:
            raise ValueError("当前 SFace 模型使用 128 维特征")
        for name in ("detector_score", "detector_nms", "similarity_threshold", "ambiguity_margin",
                     "enroll_consistency", "duplicate_threshold"):
            value = getattr(face, name)
            if not math.isfinite(value) or not 0 < value < 1:
                raise ValueError(f"face.{name} 必须在 0~1 之间")
        for name in ("processing_interval", "enroll_interval", "enroll_timeout", "enroll_indicator_seconds", "unlock_seconds"):
            value = getattr(face, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"face.{name} 必须是有限正数")
        if not math.isfinite(face.minimum_sharpness) or face.minimum_sharpness < 0:
            raise ValueError("人脸清晰度阈值必须是有限非负数")
        if face.enroll_timeout <= face.enroll_interval * (face.enroll_samples - 1):
            raise ValueError("人脸录入超时须大于多帧采集所需间隔")
        if not isinstance(face.enroll_name, str) or len(face.enroll_name) > 64:
            raise ValueError("录入姓名须为不超过 64 字的字符串")
        if face.unlock_on_match and not self.lock.enabled:
            raise ValueError("自动开锁需要启用电磁锁驱动")
        return self


DEFAULT_CONFIG = Config()
