"""Shared ADC diagnostics and electrical feedback sampling."""
import math
from statistics import median
import time


class AdcFeedbackChecks:
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


def check_current(manager):
    config = manager.config.current
    if not config.enabled:
        monitor = getattr(manager, "adc_monitor", None)
        if monitor is not None and manager.config.ads1115.enabled:
            voltage = monitor.read_all()
            return {**voltage, "status": "unconfigured", "adc_status": "normal",
                    "note": "ADS1115 四路电压已读取；设备通道和电流换算尚未确认"}
        return {"status": "unconfigured", "note": "ADS1115 电流检测尚未启用"}
    channels = {}
    for name, channel in config.channels.items():
        channels.setdefault(channel.address, name)
    if not channels:
        return {"status": "unconfigured", "note": "未配置 ADS1115 通道"}
    values = {name: manager.current_monitor.read_ma(name) for name in channels.values()}
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("电流测量结果不是有限数值")
    return {"status": "normal", "currents_ma": values, "note": "已配置 ADC 的读取和电流换算通过"}
