"""跨场景共用 RGB 的颜色及三路输出状态检查。"""
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
            checks = {}
            result = {"status": "error", "check_scope": "electrical_response",
                      "address": hex(address), "channel": channel, "colors": checks,
                      "cmd_on": bool(state.get("on")), "command": {"color": previous},
                      "calibration_basis": "normal_load_samples; disconnected_load_not_verified"}
            changed = False
            try:
                for color in ("red", "green", "blue"):
                    self._cancel(cancel_event)
                    changed = True
                    attempts = []
                    for attempt in range(2):
                        try:
                            measurement = self._measure_color(color, address, channel, cancel_event)
                        except _FeedbackUnavailable as exc:
                            measurement = {"status": "unknown", "reason": "adc_unavailable", "note": str(exc)}
                        attempts.append(measurement)
                        if measurement["status"] == "normal":
                            break
                    # 一次稳定失败、另一次不稳定，仍不足以确定灯的状态。
                    selected = next((item for item in reversed(attempts) if item["status"] == "normal"), None)
                    if selected is None:
                        selected = next((item for item in reversed(attempts) if item["status"] == "unknown"), attempts[-1])
                    checks[color] = {**selected, "attempts": attempts}
                status = ("error" if any(item["status"] == "error" for item in checks.values()) else
                          "unknown" if any(item["status"] == "unknown" for item in checks.values()) else "normal")
                summary = "，".join(
                    (f"{color}={item['delta_volts'] * 1000:.3f}mV"
                     f"(基线漂移{item['baseline_drift_volts'] * 1000:.3f}mV，{item['status']})")
                    if "delta_volts" in item else f"{color}: {item.get('note', item['status'])}"
                    for color, item in checks.items())
                verdict = {"normal": "三色电气反馈通过", "error": "稳定采样仍有颜色反馈不足",
                           "unknown": "反馈测量未能确认；不能据此判定指示灯损坏"}[status]
                result.update(status=status, note=f"ADC 0x{address:02X}/AIN{channel} {verdict}；{summary}")
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

    def _measure_color(self, color, address, channel, cancel_event):
        samples, errors = [], []
        for output in ("off", color, "off"):
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
        minimum = self.manager.config.rgb.diagnostic_minimum_delta_volts[color]
        stable = drift <= self.manager.config.rgb.diagnostic_maximum_baseline_drift_volts
        # 漂移略超常规范围时，须相对前后两个熄灯基线都达到原阈值。
        # 漂移本身不得超过该颜色的最小响应，避免大幅变化被当作灯的反馈。
        conservative_delta = running["volts"] - max(before["volts"], after["volts"])
        bounded_response = drift <= minimum and conservative_delta >= minimum
        status = ("normal" if bounded_response else "unknown" if not stable else
                  "normal" if delta >= minimum else "error")
        return {"status": status, "reason": "baseline_unstable" if status == "unknown" else "response",
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
