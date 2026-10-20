"""access 场景的执行器状态检查。"""
import math
from statistics import median
import time

from hardware.diagnostics import ActuatorCheck


class LockStatusCheck(ActuatorCheck):
    """短暂通断门锁，通过 ADS1115/A1 验证负载电气响应。"""

    def check(self, name, cancel_event=None):
        if name != "lock":
            raise ValueError("门锁检测设备名称不匹配")
        with self.manager._control_lock:
            adc = self.manager.config.ads1115
            if (getattr(self.manager, "_pintest_mode", False)
                    or name not in self.manager._outputs or not adc.enabled
                    or name not in adc.feedback_channels
                    or name not in adc.feedback_voltage_limits):
                return super().check(name, cancel_event)
            previous = self.manager.actuator_state(name)
            if (not previous.get("initialized") or previous.get("driver_error")
                    or previous.get("control_error")):
                return super().check(name, cancel_event)
            was_on = bool(previous.get("on"))
            cfg = self.manager.config.lock
            address = adc.feedback_addresses.get(name, adc.address)
            channel = adc.feedback_channels[name]
            off_max, on_min = adc.feedback_voltage_limits[name]
            result = {"status": "error", "transport": "i2c_ads1115",
                      "check_scope": "electrical_response", "address": hex(address),
                      "channel": channel, "cmd_on": was_on,
                      "maximum_off_absolute_volts": off_max,
                      "minimum_run_volts": on_min,
                      "minimum_delta_volts": cfg.diagnostic_minimum_delta_volts,
                      "calibration_basis": "three post-swap loaded cycles; mechanical movement not measured"}
            changed = False
            try:
                self._cancel(cancel_event)
                changed = True
                before = self._sample(False, address, channel, cancel_event)
                result["before"] = before
                energized = self._sample(True, address, channel, cancel_event)
                result["energized"] = energized
                after = self._sample(False, address, channel, cancel_event)
                result["after"] = after
                baseline = median((before["volts"], after["volts"]))
                delta = energized["volts"] - baseline
                drift = abs(after["volts"] - before["volts"])
                normal = (abs(before["volts"]) <= off_max
                          and abs(after["volts"]) <= off_max
                          and energized["volts"] >= on_min
                          and delta >= cfg.diagnostic_minimum_delta_volts
                          and drift <= cfg.diagnostic_maximum_baseline_drift_volts)
                result.update(status="normal" if normal else "error", delta_volts=delta,
                    baseline_drift_volts=drift,
                    note=f"ADC 0x{address:02X}/AIN{channel} 门锁电气反馈"
                         f"{'通过' if normal else '未通过'}；"
                         f"断电={before['volts'] * 1000:.3f}/{after['volts'] * 1000:.3f}mV；"
                         f"通电={energized['volts'] * 1000:.3f}mV；未测量锁舌机械动作")
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
