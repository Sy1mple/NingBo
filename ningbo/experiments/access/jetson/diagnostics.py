"""lock 场景的执行器状态检查。"""
import math
from statistics import median
import time
from hardware.diagnostics import ActuatorCheck


class LockStatusCheck(ActuatorCheck):
    def check(self, name, cancel_event=None):
        if name != "lock":
            raise ValueError("门锁检测设备名称不匹配")
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
            cfg = self.manager.config.lock
            address = adc.feedback_addresses.get(name, adc.address)
            channel = adc.feedback_channels[name]
            off_max, _ = adc.feedback_voltage_limits[name]
            result = {"status": "error", "transport": "i2c_ads1115",
                      "check_scope": "electrical_response", "address": hex(address), "channel": channel,
                      "cmd_on": was_on, "maximum_off_absolute_volts": off_max, "rounds": [],
                      "reference_minimum_delta_volts": cfg.diagnostic_minimum_delta_volts,
                      "calibration_basis": "existing_normal_load_reference; mechanical_movement_not_measured"}
            changed = False
            try:
                self._cancel(cancel_event)
                changed = True
                passed = 0
                for index in range(3):
                    self._cancel(cancel_event)
                    before = self._sample(False, address, channel, cancel_event)
                    energized = self._sample(True, address, channel, cancel_event)
                    after = self._sample(False, address, channel, cancel_event)
                    round_result = self._evaluate_round(before, energized, after, off_max)
                    result["rounds"].append(round_result)
                    passed += round_result["status"] == "normal"
                    if passed >= 2 or passed + 2 - index < 2:
                        break
                normal = passed >= 2
                reasons = {r["reason"] for r in result["rounds"] if r["status"] != "normal"}
                detail = "，".join({
                    "off_not_returned": "断电反馈未回到允许基线",
                    "insufficient_response": "未达到正常负载参考，不能确认门锁正常",
                }[reason] for reason in sorted(reasons))
                result.update(before=before, energized=energized, after=after,
                    delta_volts=round_result["delta_volts"],
                    baseline_drift_volts=round_result["baseline_drift_volts"],
                    passed_rounds=passed, status="normal" if normal else "error",
                    note=f"ADC 0x{address:02X}/AIN{channel} 门锁电气反馈{'通过' if normal else '未通过'}；"
                         f"停止={before['volts'] * 1000:.3f}/{after['volts'] * 1000:.3f}mV；"
                         f"运行={energized['volts'] * 1000:.3f}mV；"
                         f"{passed}/{len(result['rounds'])}轮通过，需两轮；"
                         f"参考增量={cfg.diagnostic_minimum_delta_volts * 1000:.3f}mV；"
                         f"{detail + '；' if detail else ''}未测量锁舌机械动作")
            except InterruptedError:
                result.update(status="unknown", note="门锁状态检测已取消")
            except Exception as exc:
                result.update(status="error", note=f"门锁状态检测失败: {exc}")
            finally:
                if changed:
                    try:
                        self.manager.control(name, on=was_on)
                        expected = self.expected_levels(previous, was_on)
                        observed = self.manager.actuator_output_levels(name)
                        if observed != expected:
                            raise RuntimeError("恢复后的门锁 GPIO 电平不一致")
                        result.update(restored_on=was_on, restored_levels=observed)
                    except Exception as exc:
                        result.update(status="error", note=f"门锁检测后恢复失败: {exc}")
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

    def _evaluate_round(self, before, energized, after, off_max):
        cfg = self.manager.config.lock
        baseline = max(before["volts"], after["volts"])
        drift = abs(after["volts"] - before["volts"])
        minimum = cfg.diagnostic_minimum_delta_volts
        values = energized["samples_volts"]
        responding = sum(value - baseline >= minimum for value in values)
        # 使用已有正常负载参考检查持续响应，不根据少量停止读数自动下调门槛。
        required = math.ceil(len(values) * .75)
        off_ok = (abs(before["volts"]) <= off_max and abs(after["volts"]) <= off_max
                  and drift <= cfg.diagnostic_maximum_baseline_drift_volts)
        reason = ("off_not_returned" if not off_ok else
                  "insufficient_response" if responding < required else "response")
        return {"status": "normal" if reason == "response" else "error", "reason": reason, "before": before,
                "energized": energized, "after": after,
                "delta_volts": energized["volts"] - baseline, "minimum_delta_volts": minimum,
                "response_samples": responding, "required_response_samples": required,
                "baseline_drift_volts": drift}

    def _sample(self, on, address, channel, event):
        self._cancel(event)
        self.manager.control("lock", on=on)
        cfg = self.manager.config.lock
        wait = cfg.diagnostic_on_settle_seconds if on else cfg.diagnostic_off_settle_seconds
        if event is None:
            time.sleep(wait)
        elif event.wait(wait):
            raise InterruptedError("检测已取消")
        expected = self.expected_levels({}, on)
        observed = self.manager.actuator_output_levels("lock")
        if observed != expected:
            raise RuntimeError("门锁 GPIO 电平与开关指令不一致")
        values = []
        for _ in range(cfg.diagnostic_sample_count):
            self._cancel(event)
            sample = self.manager.adc_monitor.read_channel(channel, address=address,
                full_scale_volts=cfg.diagnostic_full_scale_volts)
            volts = sample.get("volts")
            if (sample.get("saturated") or isinstance(volts, bool)
                    or not isinstance(volts, (int, float)) or not math.isfinite(volts)):
                raise ValueError("门锁反馈电压无效或超出 ADC 量程")
            values.append(volts)
        return {"volts": median(values), "samples_volts": values, "levels": observed}

    def expected_levels(self, state, command_on):
        config = self.manager.config.lock
        return {config.pin: int(command_on == config.active_high)}
