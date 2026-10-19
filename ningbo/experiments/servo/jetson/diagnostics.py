"""舵机手动检测：硬件 PWM 配置与 ADS1115/A0 运行反馈。"""
import math
from statistics import median
import time
from hardware.diagnostics import ThreadedActuatorCheck


class ServoStatusCheck(ThreadedActuatorCheck):
    def check(self, name, cancel_event=None):
        if name != "servo":
            raise ValueError("舵机检测设备名称不匹配")
        with self.manager._control_lock:
            adc = self.manager.config.ads1115
            if (getattr(self.manager, "_pintest_mode", False)
                    or name not in self.manager._outputs or not adc.enabled
                    or name not in adc.feedback_channels
                    or name not in adc.feedback_voltage_limits):
                return super().check(name, cancel_event)
            previous = self.manager.actuator_state(name)
            cfg = self.manager.config.servo
            address = adc.feedback_addresses.get(name, adc.address)
            channel = adc.feedback_channels[name]
            off_max, on_min = adc.feedback_voltage_limits[name]
            result = {"status": "error", "check_scope": "electrical_response",
                      "transport": "i2c_ads1115", "address": hex(address), "channel": channel,
                      "cmd_on": bool(previous.get("on")), "positions": [],
                      "maximum_off_volts": off_max, "minimum_run_volts": on_min,
                      "minimum_delta_volts": cfg.diagnostic_minimum_delta_volts,
                      "calibration_basis": "normal_load_samples; disconnected_load_not_verified"}
            prepared = False
            try:
                self._cancel(cancel_event)
                # This path is reached only by a manual diagnostic request.
                # Startup and snapshots still leave the PWM uninitialized.
                self.manager.prepare_actuator(name)
                prepared = True
                before = self._sample(None, address, channel, cancel_event)
                result["before"] = before
                for angle in (cfg.sweep_min_angle, cfg.default_angle, cfg.sweep_max_angle):
                    sample = self._sample(angle, address, channel, cancel_event)
                    result["positions"].append({"angle": angle, **sample})
                after = self._sample(None, address, channel, cancel_event)
                result["after"] = after
                baseline = median((before["volts"], after["volts"]))
                drift = abs(after["volts"] - before["volts"])
                off_ok = (0 <= before["volts"] <= off_max and 0 <= after["volts"] <= off_max
                          and drift <= cfg.diagnostic_maximum_baseline_drift_volts)
                for sample in result["positions"]:
                    delta = sample["volts"] - baseline
                    sample.update(delta_volts=delta,
                        status="normal" if sample["volts"] >= on_min
                        and delta >= cfg.diagnostic_minimum_delta_volts else "error")
                normal = off_ok and all(p["status"] == "normal" for p in result["positions"])
                summary = "，".join(f"{p['angle']:g}°={p['volts'] * 1000:.3f}mV"
                                   for p in result["positions"])
                result.update(status="normal" if normal else "error", baseline_drift_volts=drift,
                    note=f"ADC 0x{address:02X}/AIN{channel} 舵机电气反馈{'通过' if normal else '未通过'}；"
                         f"停止={before['volts'] * 1000:.3f}/{after['volts'] * 1000:.3f}mV；{summary}；未测量实际转角")
            except InterruptedError:
                result.update(status="unknown", note="舵机状态检测已取消")
            except Exception as exc:
                result.update(status="error", note=f"舵机状态检测失败: {exc}")
            finally:
                if prepared:
                    try:
                        if previous.get("on"):
                            if previous.get("sweeping"):
                                self.manager.control(name, sweep=True)
                                result["restored_mode"] = "sweep"
                            else:
                                self.manager.control(name, angle=previous["angle"])
                                result["restored_mode"] = "hold"
                        else:
                            self.manager.stop_actuator(name)
                            result["restored_mode"] = "off"
                    except Exception as exc:
                        result.update(status="error", note=f"舵机检测后恢复状态失败: {exc}")
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

    def _sample(self, angle, address, channel, cancel_event):
        self._cancel(cancel_event)
        if angle is None:
            self.manager.stop_actuator("servo")
        else:
            self.manager.control("servo", angle=angle)
        cfg = self.manager.config.servo
        if cancel_event is None:
            time.sleep(cfg.diagnostic_settle_seconds)
        elif cancel_event.wait(cfg.diagnostic_settle_seconds):
            raise InterruptedError("检测已取消")
        state = self.manager.actuator_state("servo")
        on = angle is not None
        driver = self._check_driver("servo", state, on, state.get("driver_error") or state.get("control_error"))
        signal = self._check_signal("servo", state, on)
        if driver["status"] != "normal" or signal["status"] != "normal":
            raise RuntimeError(driver["note"] if driver["status"] != "normal" else signal["note"])
        values = []
        for _ in range(cfg.diagnostic_sample_count):
            self._cancel(cancel_event)
            sample = self.manager.adc_monitor.read_channel(channel, address=address,
                full_scale_volts=cfg.diagnostic_full_scale_volts)
            volts = sample.get("volts")
            if (sample.get("saturated") or isinstance(volts, bool)
                    or not isinstance(volts, (int, float)) or not math.isfinite(volts)):
                raise ValueError("舵机反馈电压无效或超出 ADC 量程")
            values.append(volts)
        return {"volts": median(values), "samples_volts": values,
                "driver": driver, "signal": signal}

    def _check_driver(self, name, state, command_on, error):
        if error:
            return {"status": "error", "note": error}
        if state.get("backend") == "hardware_pwm":
            if command_on and not state.get("pwm_ready"):
                return {"status": "error", "note": "舵机硬件 PWM 未初始化"}
            if state.get("sweeping") and not state.get("worker_alive"):
                return {"status": "error", "note": "舵机往返角度更新线程未运行"}
            return {"status": "normal", "note": "舵机使用硬件 PWM 控制"}
        return super()._check_driver(name, state, command_on, error)

    def _check_signal(self, name, state, command_on):
        if state.get("backend") == "hardware_pwm":
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
        return super()._check_signal(name, state, command_on)
