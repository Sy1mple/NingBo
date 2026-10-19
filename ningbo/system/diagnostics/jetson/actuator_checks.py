"""兼容执行器检测入口；这里只注册并分发到设备所属场景。"""
from experiments.servo.jetson.diagnostics import ServoStatusCheck
from experiments.stepper.jetson.diagnostics import StepperStatusCheck
from experiments.fan.jetson.diagnostics import FanStatusCheck
from experiments.access.jetson.diagnostics import LockStatusCheck
from experiments.ultrasonic.jetson.diagnostics import BuzzerStatusCheck
from hardware.rgb.diagnostics import RgbStatusCheck

ACTUATOR_CHECKS = {
    "servo": ServoStatusCheck, "stepper": StepperStatusCheck,
    "fan": FanStatusCheck, "lock": LockStatusCheck,
    "buzzer": BuzzerStatusCheck, "rgb": RgbStatusCheck,
}


class ActuatorStatusChecker:
    STATIC_OUTPUTS = ("fan", "lock", "buzzer", "rgb")
    THREADED_OUTPUTS = ("servo", "stepper")

    def __init__(self, manager):
        self.manager = manager
        self._checks = {name: cls(manager) for name, cls in ACTUATOR_CHECKS.items()}

    def check(self, name, cancel_event=None):
        return self._checks[name].check(name, cancel_event=cancel_event)
