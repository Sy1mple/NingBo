"""fan 场景的执行器状态检查。"""
import math
from statistics import median
import time
from hardware.diagnostics import ActuatorCheck


class FanStatusCheck(ActuatorCheck):
    def check(self, name, cancel_event=None):
        if name != "fan":
            raise ValueError("风扇检测设备名称不匹配")
        with self.control_session(name, cancel_event):
            adc = self.manager.config.ads1115
            if (getattr(self.manager, "_pintest_mode", False)
                    or name not in self.manager._outputs or not adc.enabled
                    or name not in adc.feedback_channels or name not in adc.feedback_voltage_limits):
                return super().check(name, cancel_event)
            previous = self.manager.actuator_state(name)
            if not previous.get("initialized") or previous.get("driver_error") or previous.get("control_error"):
                return super().check(name, cancel_event)
            was_on = bool(previous.get("on"))
            cfg = self.manager.config.fan
            address = adc.feedback_addresses.get(name, adc.address)
            channel = adc.feedback_channels[name]
            off_max, _ = adc.feedback_voltage_limits[name]
            result = {"status": "error", "transport": "i2c_ads1115",
                      "check_scope": "electrical_response", "address": hex(address), "channel": channel,
                      "cmd_on": was_on, "maximum_off_absolute_volts": off_max, "rounds": [],
                      "calibration_basis": "per_round_off_noise; disconnected_load_not_verified"}
            changed = False
            try:
                self._cancel(cancel_event)
                changed = True
                passed = 0
                for index in range(3):
                    self._cancel(cancel_event)
                    before = self._sample(False, address, channel, cancel_event)
                    running = self._sample(True, address, channel, cancel_event)
                    after = self._sample(False, address, channel, cancel_event)
                    round_result = self._evaluate_round(before, running, after, off_max)
                    result["rounds"].append(round_result)
                    passed += round_result["status"] == "normal"
                    if passed >= 2 or passed + 2 - index < 2:
                        break
                normal = passed >= 2
                result.update(before=before, running=running, after=after,
                    delta_volts=round_result["delta_volts"],
                    baseline_drift_volts=round_result["baseline_drift_volts"],
                    passed_rounds=passed, status="normal" if normal else "error",
                    note=f"ADC 0x{address:02X}/AIN{channel} 风扇电气反馈{'通过' if normal else '未通过'}；"
                         f"停止={before['volts'] * 1000:.3f}/{after['volts'] * 1000:.3f}mV；"
                         f"运行={running['volts'] * 1000:.3f}mV；"
                         f"{passed}/{len(result['rounds'])}轮通过，需两轮；"
                         f"本轮噪声阈值={round_result['minimum_delta_volts'] * 1000:.3f}mV；未测量转速或风量")
            except InterruptedError:
                result.update(status="unknown", note="风扇状态检测已取消")
            except Exception as exc:
                result.update(status="error", note=f"风扇状态检测失败: {exc}")
            finally:
                if changed:
                    try:
                        self.manager.control(name, on=was_on)
                        expected = self.expected_levels(previous, was_on)
                        observed = self.manager.actuator_output_levels(name)
                        if observed != expected:
                            raise RuntimeError("恢复后的风扇 GPIO 电平不一致")
                        result.update(restored_on=was_on, restored_levels=observed)
                    except Exception as exc:
                        result.update(status="error", note=f"风扇检测后恢复失败: {exc}")
                        try:
                            self.manager.stop_actuator(name)
                        except Exception as stop_exc:
                            result["stop_error"] = str(stop_exc)
            self.manager.record_actuator_diagnostic(name, result)
            return result

    @staticmethod
    def _cancel(event):
        if event is not None and event.is_set():
            raise InterruptedError("检测已取消")

    def _evaluate_round(self, before, running, after, off_max):
        cfg = self.manager.config.fan
        baseline = max(before["volts"], after["volts"])
        drift = abs(after["volts"] - before["volts"])
        noise = max(median(abs(v - group["volts"]) for v in group["samples_volts"])
                    for group in (before, after)) * 1.4826
        minimum = max(8 * cfg.diagnostic_full_scale_volts / 32768,
                      cfg.diagnostic_noise_multiplier * noise)
        values = running["samples_volts"]
        responding = sum(value - baseline >= minimum for value in values)
        # 风扇运行反馈须持续存在，单次启动尖峰不能代替运行响应。
        required = math.ceil(len(values) * .75)
        normal = (abs(before["volts"]) <= off_max and abs(after["volts"]) <= off_max
                  and drift <= cfg.diagnostic_maximum_baseline_drift_volts and responding >= required)
        return {"status": "normal" if normal else "error", "before": before,
                "running": running, "after": after, "noise_volts": noise,
                "delta_volts": running["volts"] - baseline, "minimum_delta_volts": minimum,
                "response_samples": responding, "required_response_samples": required,
                "baseline_drift_volts": drift}

    def _sample(self, on, address, channel, event):
        self._cancel(event)
        self.manager.control("fan", on=on)
        cfg = self.manager.config.fan
        wait = cfg.diagnostic_on_settle_seconds if on else cfg.diagnostic_off_settle_seconds
        if event is None:
            time.sleep(wait)
        elif event.wait(wait):
            raise InterruptedError("检测已取消")
        expected = self.expected_levels({}, on)
        observed = self.manager.actuator_output_levels("fan")
        if observed != expected:
            raise RuntimeError("风扇 GPIO 电平与开关指令不一致")
        values = []
        for _ in range(cfg.diagnostic_sample_count):
            self._cancel(event)
            sample = self.manager.adc_monitor.read_channel(channel, address=address,
                full_scale_volts=cfg.diagnostic_full_scale_volts)
            volts = sample.get("volts")
            if (sample.get("saturated") or isinstance(volts, bool)
                    or not isinstance(volts, (int, float)) or not math.isfinite(volts)):
                raise ValueError("风扇反馈电压无效或超出 ADC 量程")
            values.append(volts)
        return {"volts": median(values), "samples_volts": values, "levels": observed}

    def expected_levels(self, state, command_on):
        config = self.manager.config.fan
        return {config.pin: int(command_on == config.active_high)}
