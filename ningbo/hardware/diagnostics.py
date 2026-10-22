"""Shared diagnostic mechanics; scene modules own device-specific decisions."""
import math
from hardware.adc.diagnostics import AdcFeedbackChecks


def check_numeric_sensor(manager, name, fields, in_range):
    state = manager.poll_sensor(name)
    if state.get("status") in ("error", "disabled"):
        return state
    if not all(isinstance(state.get(field), (int, float))
               and not isinstance(state.get(field), bool)
               and math.isfinite(state[field]) for field in fields):
        return {**state, "status": "error", "note": "测量值缺失或无效"}
    if not in_range(state):
        return {**state, "status": "error", "note": "测量值超出配置范围"}
    return {**state, "status": "normal", "note": "采集及驱动校验通过"}


class ActuatorCheck(AdcFeedbackChecks):
    def __init__(self, manager):
        self.manager = manager

    def control_session(self, name, cancel_event=None):
        session = getattr(self.manager, "diagnostic_session", None)
        return session(name, cancel_event) if session is not None else self.manager._control_lock

    def check(self, name, cancel_event=None):
        with self.control_session(name, cancel_event):
            if getattr(self.manager, "_pintest_mode", False):
                return {"status": "unknown", "note": "引脚维护模式下暂停执行器状态检测"}
            if name not in self.manager._outputs:
                return {"status": "disabled", "current_ma": None, "note": "执行器未启用"}
            state = self.manager.actuator_state(name)
            if not state.get('initialized', False):
                result = {'status': 'error' if state.get('control_error') else 'unknown',
                          'note': state.get('control_error') or '执行器尚未手动启用，未初始化 GPIO/PWM；状态检测不主动启动设备',
                          'initialized': False, 'current_ma': None, 'check_scope': 'not_initialized'}
                self.manager.record_actuator_diagnostic(name, result)
                return result
            command_on = bool(state.get("on") or state.get("running"))
            command = {key: state[key] for key in ("on", "running", "angle", "color", "dir", "n", "spd") if key in state}
            driver_error = state.get("driver_error") or state.get("control_error")
            driver = self._check_driver(name, state, command_on, driver_error)
            signal = self._check_signal(name, state, command_on)
            if driver["status"] == "error" or signal["status"] == "error":
                feedback = {"status": "unknown", "note": "先处理驱动或输出信号异常"}
            else:
                feedback = self._check_current(name, command_on, cancel_event)
            failed = next((item for item in (driver, signal, feedback) if item["status"] == "error"), None)
            if failed is not None:
                status, note = "error", failed["note"]
            elif feedback["status"] in ("unconfigured", "unknown"):
                status, note = feedback["status"], feedback["note"]
            elif signal["status"] == "unknown":
                status, note = "unknown", signal["note"]
            else:
                status, note = feedback["status"], feedback["note"]
            result = {
                "status": status, "note": note, "cmd_on": command_on,
                "command": command, "driver": driver, "signal": signal, "feedback": feedback,
                "current_ma": feedback.get("current_ma"), "check_scope": "electrical",
            }
            self.manager.record_actuator_diagnostic(name, result)
            return result

    def _check_driver(self, name, state, command_on, error):
        if error:
            return {"status": "error", "note": error}
        return {"status": "normal", "note": "未记录当前驱动或控制错误"}

    def _check_signal(self, name, state, command_on):
        if not self.manager.config.actuator_diagnostics.gpio_readback:
            return {"status": "disabled", "note": "GPIO 读回检查未启用"}
        try:
            expected = self.expected_levels(state, command_on)
        except ValueError as exc:
            return {"status": "error", "note": str(exc)}
        try:
            observed = self.manager.actuator_output_levels(name)
        except Exception as exc:
            return {"status": "error", "note": f"执行器 GPIO 读回失败: {exc}", "expected": expected}
        normal = observed == expected
        return {"status": "normal" if normal else "error", "expected": expected, "observed": observed,
                "note": "输出电平与控制状态一致" if normal else "输出电平与控制状态不一致"}

    def expected_levels(self, state, command_on):
        raise NotImplementedError


class ThreadedActuatorCheck(ActuatorCheck):
    def _check_driver(self, name, state, command_on, error):
        if error:
            return {"status": "error", "note": error}
        if command_on:
            if not state.get("worker_alive"):
                return {"status": "error", "note": "执行器收到运行指令，但控制线程未运行"}
            return {"status": "normal", "worker_alive": True, "note": "执行器控制线程运行中"}
        return super()._check_driver(name, state, command_on, error)

    def _check_signal(self, name, state, command_on):
        return {"status": "disabled", "note": "动态脉冲信号通过线程状态及电流反馈检测"}
