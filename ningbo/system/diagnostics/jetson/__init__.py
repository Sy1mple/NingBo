"""状态检测公共接口。"""
from system.diagnostics.jetson.service import DiagnosticService
from system.diagnostics.jetson.sensor_checks import SensorStatusChecker
from system.diagnostics.jetson.actuator_checks import ActuatorStatusChecker

__all__ = ["DiagnosticService", "SensorStatusChecker", "ActuatorStatusChecker"]
