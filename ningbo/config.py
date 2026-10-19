"""Configuration composition only. Edit defaults and rules in their owning scene."""
from dataclasses import dataclass, field
from experiments.environment.jetson.config import Dht11Config, LightConfig
from experiments.body_temperature.jetson.config import BodyTemperatureConfig
from experiments.ultrasonic.jetson.config import UltrasonicConfig
from experiments.smoke.jetson.config import SmokeConfig
from experiments.servo.jetson.config import ServoConfig
from experiments.stepper.jetson.config import StepperConfig
from experiments.voice.jetson.config import _voice_model_path, VoiceConfig
from experiments.access.jetson.config import CameraConfig, _face_model_path, _face_database_path, FaceConfig
from experiments.fan.jetson.config import FanConfig
from experiments.access.jetson.config import LockConfig
from experiments.ultrasonic.jetson.config import BuzzerConfig
from experiments.servo.serial_screen.config import ScreenControlConfig
from hardware.rgb.config import RgbConfig, RgbFeedbackConfig
from hardware.adc.config import CurrentChannel, CurrentConfig, Ads1115Config
from hardware.screen.config import ScreenConfig
from system.diagnostics.jetson.config import ActuatorDiagnosticConfig
from hardware.validation import validate as validate_connections
from experiments.environment.jetson.config import validate as validate_environment
from experiments.body_temperature.jetson.config import validate as validate_body_temperature
from experiments.ultrasonic.jetson.config import validate as validate_ultrasonic
from experiments.smoke.jetson.config import validate as validate_smoke
from experiments.servo.jetson.config import validate as validate_servo
from experiments.stepper.jetson.config import validate as validate_stepper
from experiments.voice.jetson.config import validate as validate_voice
from experiments.access.jetson.config import validate as validate_access
from experiments.fan.jetson.config import validate as validate_fan
from hardware.rgb.config import validate as validate_rgb
from hardware.adc.config import validate as validate_adc
from hardware.screen.config import validate as validate_screen
from experiments.servo.serial_screen.config import validate as validate_directions
from system.diagnostics.jetson.config import validate as validate_diagnostics


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
    buzzer: BuzzerConfig = field(default_factory=BuzzerConfig)
    lock: LockConfig = field(default_factory=LockConfig)
    fan: FanConfig = field(default_factory=FanConfig)
    rgb: RgbConfig = field(default_factory=RgbConfig)
    current: CurrentConfig = field(default_factory=CurrentConfig)
    ads1115: Ads1115Config = field(default_factory=Ads1115Config)
    actuator_diagnostics: ActuatorDiagnosticConfig = field(default_factory=ActuatorDiagnosticConfig)
    rgb_feedback: RgbFeedbackConfig = field(default_factory=RgbFeedbackConfig)
    screen_control: ScreenControlConfig = field(default_factory=ScreenControlConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    face: FaceConfig = field(default_factory=FaceConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)

    def gpio_assignments(self):
        from importlib import import_module
        from experiments import EXPERIMENTS
        modules = [import_module(f"experiments.{name}.jetson.config") for name in EXPERIMENTS]
        modules.append(import_module("hardware.rgb.config"))
        return [pair for module in modules if hasattr(module, "pin_assignments")
                for pair in module.pin_assignments(self)]

    def gpio_devices(self):
        return dict(self.gpio_assignments())

    def validate(self):
        for validator in (
            validate_connections, validate_screen, validate_environment,
            validate_body_temperature, validate_ultrasonic, validate_smoke,
            validate_servo, validate_stepper, validate_adc,
            validate_diagnostics, validate_rgb, validate_directions,
            validate_voice, validate_access, validate_fan,
        ):
            validator(self)
        return self


DEFAULT_CONFIG = Config()
