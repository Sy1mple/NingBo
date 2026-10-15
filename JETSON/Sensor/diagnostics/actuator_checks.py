"""执行器状态检测：驱动、线程、输出读回和真实反馈，按设备类型判断。"""
import math
from statistics import median
import time

from ..actuators.outputs import RgbDriver


class ActuatorStatusChecker:
    STATIC_OUTPUTS = ("fan", "lock", "buzzer", "rgb")
    THREADED_OUTPUTS = ("servo", "stepper")

    def __init__(self, manager):
        self.manager = manager

    def check(self, name, cancel_event=None):
        if name == "oled":
            return self._check_oled()
        with self.manager._control_lock:
            if getattr(self.manager, "_pintest_mode", False):
                return {"status": "unknown", "note": "引脚维护模式下暂停执行器状态检测"}
            if name not in self.manager._outputs:
                return {"status": "disabled", "current_ma": None, "note": "执行器未启用"}
            state = self.manager.actuator_state(name)
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
        if name == "servo" and state.get("backend") == "hardware_pwm":
            if command_on and not state.get("pwm_ready"):
                return {"status": "error", "note": "舵机硬件 PWM 未初始化"}
            if state.get("sweeping") and not state.get("worker_alive"):
                return {"status": "error", "note": "舵机往返角度更新线程未运行"}
            return {"status": "normal", "note": "舵机使用硬件 PWM 控制"}
        if name in self.THREADED_OUTPUTS and command_on:
            if not state.get("worker_alive"):
                return {"status": "error", "note": "执行器收到运行指令，但控制线程未运行"}
            return {"status": "normal", "worker_alive": True, "note": "执行器控制线程运行中"}
        return {"status": "normal", "note": "未记录当前驱动或控制错误"}

    def _check_signal(self, name, state, command_on):
        policy = self.manager.config.actuator_diagnostics
        if name == "servo" and state.get("backend") == "hardware_pwm":
            observed = state.get("pwm_state")
            if state.get("pwm_read_error") or not observed:
                return {"status": "error", "note": "舵机 PWM 控制器状态读取失败"}
            expected = {"enable": int(command_on)}
            if command_on:
                expected.update(period=state["period_ns"], duty_cycle=round(state["pulse_us"] * 1000))
            normal = all(isinstance(observed.get(key), int)
                         and abs(observed[key] - value) <= (1 if key == "duty_cycle" else 0)
                         for key, value in expected.items())
            return {"status": "normal" if normal else "error", "expected": expected, "observed": observed,
                    "note": "PWM 控制器配置与命令一致；未测量实际角度" if normal else "PWM 控制器配置与命令不一致"}
        if name in self.THREADED_OUTPUTS:
            return {"status": "disabled", "note": "动态脉冲信号通过线程状态及电流反馈检测"}
        if not policy.gpio_readback:
            return {"status": "disabled", "note": "GPIO 读回检查未启用"}
        config = getattr(self.manager.config, name)
        if name == "rgb":
            pattern = RgbDriver.COLORS.get(state.get("color", "off"))
            if pattern is None:
                return {"status": "error", "note": "RGB 控制颜色状态无效"}
            expected = {pin: int(bool(value) == config.active_high) for pin, value in zip(config.pins, pattern)}
        else:
            expected = {config.pin: int(command_on == config.active_high)}
        try:
            observed = self.manager.actuator_output_levels(name)
        except Exception as exc:
            return {"status": "error", "note": f"执行器 GPIO 读回失败: {exc}", "expected": expected}
        normal = observed == expected
        return {"status": "normal" if normal else "error", "expected": expected, "observed": observed,
                "note": "输出电平与控制状态一致" if normal else "输出电平与控制状态不一致"}

    def _check_current(self, name, command_on, cancel_event):
        config = self.manager.config.current
        if not config.enabled or name not in config.channels:
            adc_config = self.manager.config.ads1115
            monitor = getattr(self.manager, "adc_monitor", None)
            if monitor is not None and adc_config.enabled and name in adc_config.feedback_channels:
                return self._check_voltage(name, command_on, cancel_event)
            return {"status": "unconfigured", "note": "缺少已启用的执行器电流反馈通道"}
        channel = config.channels[name]
        policy = self.manager.config.actuator_diagnostics
        samples = []
        try:
            for index in range(policy.sample_count):
                if cancel_event is not None and cancel_event.is_set():
                    return {"status": "unknown", "note": "执行器状态检测已取消"}
                current = self.manager.current_monitor.read_ma(name)
                if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current):
                    raise ValueError("执行器反馈电流无效")
                samples.append(current)
                if index + 1 < policy.sample_count and policy.sample_interval:
                    if cancel_event is None:
                        time.sleep(policy.sample_interval)
                    elif cancel_event.wait(policy.sample_interval):
                        return {"status": "unknown", "note": "执行器状态检测已取消"}
        except Exception as exc:
            return {"status": "error", "note": f"执行器电流读取失败: {exc}", "samples_ma": samples}
        current = median(samples)
        absolute_current = median(abs(value) for value in samples)
        peak = max(abs(value) for value in samples)
        off_limit = channel.threshold_ma if channel.off_current_ma is None else channel.off_current_ma
        if channel.max_current_ma is not None and peak > channel.max_current_ma:
            status, note = "error", "反馈电流超过该执行器的运行上限"
        elif command_on and current < channel.threshold_ma:
            status, note = "error", "运行指令已发出，但反馈电流不足"
        elif command_on:
            status, note = "normal", "当前运行指令的电流反馈通过；检测范围为电气状态"
        elif absolute_current >= off_limit:
            status, note = "error", "关闭指令下仍有明显电流"
        else:
            status, note = "idle", "当前关闭且电流低；尚未检测运行状态"
        return {"status": status, "note": note, "current_ma": current,
                "absolute_current_ma": absolute_current, "peak_current_ma": peak,
                "samples_ma": samples, "minimum_run_ma": channel.threshold_ma,
                "maximum_run_ma": channel.max_current_ma, "maximum_off_ma": off_limit}

    def _check_voltage(self, name, command_on, cancel_event):
        config = self.manager.config.ads1115
        channel = config.feedback_channels[name]
        address = config.feedback_addresses.get(name, config.address)
        location = f"ADC 0x{address:02X}/AIN{channel}"
        policy = self.manager.config.actuator_diagnostics
        samples = []
        try:
            for index in range(policy.sample_count):
                if cancel_event is not None and cancel_event.is_set():
                    return {"status": "unknown", "note": "电压反馈检测已取消"}
                sample = self.manager.adc_monitor.read_channel(channel, address=address)
                if sample['saturated'] or not math.isfinite(sample['volts']):
                    raise ValueError("电压反馈超出量程或无效")
                samples.append(sample['volts'])
                if index + 1 < policy.sample_count and policy.sample_interval:
                    if cancel_event is None:
                        time.sleep(policy.sample_interval)
                    elif cancel_event.wait(policy.sample_interval):
                        return {"status": "unknown", "note": "电压反馈检测已取消"}
        except Exception as exc:
            return {"status": "error", "channel": channel, "address": hex(address), "type": "adc_voltage",
                    "samples_volts": samples, "note": f"{location} 电压反馈读取失败: {exc}"}
        volts = median(samples)
        result = {"channel": channel, "address": hex(address), "type": "adc_voltage", "volts": volts,
                  "samples_volts": samples, "sample_time": sample['sample_time']}
        limits = config.feedback_voltage_limits.get(name)
        if limits is None:
            return {**result, "status": "unknown", "note": f"{location}={volts:.6f}V；反馈阈值未标定"}
        off_max, on_min = limits
        if command_on and volts >= on_min:
            status, note = "normal", "运行电压反馈与指令一致；未验证机械动作"
        elif not command_on and volts <= off_max:
            status, note = "idle", "控制输出已关闭且电压反馈低；不表示设备电源断电"
        elif command_on and volts <= off_max:
            status, note = "error", "已下发运行指令，但电压反馈不足"
        elif not command_on and volts >= on_min:
            status, note = "error", "控制输出已关闭，但电压反馈仍处于运行区间"
        else:
            status, note = "unknown", "电压反馈处于阈值之间，需复测"
        return {**result, "status": status, "maximum_off_volts": off_max,
                "minimum_run_volts": on_min, "note": f"{location}={volts:.6f}V；{note}"}

    def _check_oled(self):
        if not self.manager.config.oled.enabled:
            return {"status": "disabled", "check_scope": "communication", "note": "OLED 未启用"}
        state = self.manager.poll_sensor("oled")
        normal = state.get("ok") is True and state.get("status") != "error"
        return {**state, "status": "normal" if normal else "error", "check_scope": "communication",
                "feedback": {"status": "normal" if normal else "error", "type": "i2c"},
                "note": "OLED I2C 应答正常" if normal else state.get("note", "OLED I2C 无有效应答")}
