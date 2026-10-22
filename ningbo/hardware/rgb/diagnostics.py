"""RGB 三路同时点亮，检查输出电平及总电气反馈。"""
import math
from statistics import median
import time
from hardware.diagnostics import ActuatorCheck
from hardware.rgb.gpio import RgbDriver


class _FeedbackUnavailable(RuntimeError):
    """ADC 测量不可用，与 RGB GPIO 输出失败分开报告。"""


class RgbStatusCheck(ActuatorCheck):
    def check(self, name, cancel_event=None):
        if name != "rgb":
            raise ValueError("RGB 检测设备名称不匹配")
        with self.control_session(name, cancel_event):
            state = self.manager.actuator_state(name)
            if (getattr(self.manager, "_pintest_mode", False)
                    or name not in self.manager._outputs or not state.get("initialized", False)):
                return super().check(name, cancel_event)
            if state.get("driver_error") or state.get("control_error"):
                return super().check(name, cancel_event)
            adc = self.manager.config.ads1115
            if not adc.enabled or name not in adc.feedback_channels:
                return super().check(name, cancel_event)
            previous = state.get("color", "off")
            if previous not in RgbDriver.COLORS:
                raise ValueError("RGB 控制颜色状态无效")
            channel = adc.feedback_channels[name]
            address = adc.feedback_addresses.get(name, adc.address)
            result = {"status": "error", "check_scope": "electrical_response",
                      "address": hex(address), "channel": channel,
                      "test_mode": "simultaneous", "test_color": "white",
                      "cmd_on": bool(state.get("on")), "command": {"color": previous},
                      "calibration_basis": "sum_of_existing_color_minima; simultaneous_load_not_calibrated"}
            changed = False
            try:
                attempts = []
                for attempt in range(2):
                    self._cancel(cancel_event)
                    changed = True
                    try:
                        measurement = self._measure_all_on(address, channel, cancel_event)
                    except _FeedbackUnavailable as exc:
                        measurement = {"status": "error", "reason": "adc_unavailable", "note": str(exc)}
                    attempts.append(measurement)
                    if measurement["status"] == "normal":
                        break
                selected = attempts[-1]
                summary = (
                    f"总反馈={selected['conservative_delta_volts'] * 1000:.3f}mV，"
                    f"阈值={selected['minimum_delta_volts'] * 1000:.3f}mV，"
                    f"基线变化={selected['baseline_drift_volts'] * 1000:.3f}mV"
                    if "conservative_delta_volts" in selected else selected["note"])
                verdict = "同时点亮电气反馈通过" if selected["status"] == "normal" else "同时点亮电气反馈异常"
                result.update(status=selected["status"], feedback=selected, attempts=attempts,
                              note=f"ADC 0x{address:02X}/AIN{channel} {verdict}；{summary}")
            except InterruptedError:
                result.update(status="unknown", note="指示灯状态检测已取消")
            except Exception as exc:
                result.update(status="error", note=f"指示灯状态检测失败: {exc}")
            finally:
                if changed:
                    try:
                        self.manager.control(name, color=previous)
                        result["restored_color"] = previous
                    except Exception as exc:
                        result.update(status="error", note=f"指示灯检测后恢复颜色失败: {exc}")
                        try:
                            self.manager.stop_actuator(name)
                        except Exception as stop_exc:
                            result["stop_error"] = str(stop_exc)
            self.manager.record_actuator_diagnostic(name, result)
            return result

    def _measure_all_on(self, address, channel, cancel_event):
        samples, errors = [], []
        for output in ("off", "white", "off"):
            try:
                samples.append(self._sample(output, address, channel, cancel_event))
            except _FeedbackUnavailable as exc:
                errors.append(str(exc))
        if errors:
            raise _FeedbackUnavailable("；".join(errors))
        before, running, after = samples
        baseline = median((before["volts"], after["volts"]))
        delta = running["volts"] - baseline
        drift = abs(after["volts"] - before["volts"])
        # 同时点亮使用三路既有最小反馈之和，不再分别轮测颜色。
        minimum = sum(self.manager.config.rgb.diagnostic_minimum_delta_volts.values())
        # 必须高于前后两次熄灯基线，不能仅靠基线平均值制造有效反馈。
        conservative_delta = running["volts"] - max(before["volts"], after["volts"])
        status = "normal" if conservative_delta >= minimum else "error"
        return {"status": status, "reason": "response",
                "before": before, "running": running, "after": after,
                "delta_volts": delta, "minimum_delta_volts": minimum,
                "conservative_delta_volts": conservative_delta,
                "baseline_drift_volts": drift}

    @staticmethod
    def _cancel(event):
        if event is not None and event.is_set():
            raise InterruptedError("检测已取消")

    def _sample(self, color, address, channel, cancel_event):
        self._cancel(cancel_event)
        self.manager.control("rgb", color=color)
        config = self.manager.config.rgb
        if cancel_event is None:
            time.sleep(config.diagnostic_settle_seconds)
        elif cancel_event.wait(config.diagnostic_settle_seconds):
            raise InterruptedError("检测已取消")
        state = self.manager.actuator_state("rgb")
        if state.get("color") != color:
            raise RuntimeError(f"{color} RGB 状态与控制指令不一致")
        expected = self.expected_levels(state, color != "off")
        observed = self.manager.actuator_output_levels("rgb")
        if observed != expected:
            raise RuntimeError(f"{color} GPIO 输出与控制指令不一致")
        values = []
        for _ in range(config.diagnostic_sample_count):
            self._cancel(cancel_event)
            try:
                sample = self.manager.adc_monitor.read_channel(channel, address=address,
                    full_scale_volts=config.diagnostic_full_scale_volts)
            except Exception as exc:
                raise _FeedbackUnavailable(f"{color} ADC 读取失败: {exc}") from exc
            volts = sample.get("volts")
            if (sample.get("saturated") or isinstance(volts, bool)
                    or not isinstance(volts, (int, float)) or not math.isfinite(volts)):
                raise _FeedbackUnavailable("A3 电压无效或超出 ADC 量程")
            values.append(volts)
        return {"volts": median(values), "samples_volts": values, "gpio": observed}

    def expected_levels(self, state, command_on):
        config = self.manager.config.rgb
        pattern = RgbDriver.COLORS.get(state.get("color", "off"))
        if pattern is None:
            raise ValueError("RGB 控制颜色状态无效")
        return {pin: int(bool(value) == config.active_high)
                for pin, value in zip(config.pins, pattern)}
