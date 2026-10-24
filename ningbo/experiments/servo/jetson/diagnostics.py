"""舵机手动检测：硬件 PWM 配置与 ADS1115/A0 运行反馈。"""
import math
from contextlib import contextmanager
from statistics import median
import time
from hardware.diagnostics import ThreadedActuatorCheck


class ServoStatusCheck(ThreadedActuatorCheck):
    def check(self, name, cancel_event=None):
        if name != "servo":
            raise ValueError("舵机检测设备名称不匹配")
        with self.control_session(name, cancel_event):
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
            off_max, _ = adc.feedback_voltage_limits[name]
            result = {"status": "error", "check_scope": "electrical_response",
                      "transport": "i2c_ads1115", "address": hex(address), "channel": channel,
                      "cmd_on": bool(previous.get("on")), "positions": [], "rounds": [],
                      "maximum_off_volts": off_max,
                      "reference_minimum_delta_volts": cfg.diagnostic_minimum_delta_volts,
                      "calibration_basis": "existing_normal_load_reference; current_setup_not_revalidated"}
            prepared = False
            try:
                self._cancel(cancel_event)
                # This path is reached only by a manual diagnostic request.
                # Startup and snapshots still leave the PWM uninitialized.
                self.manager.prepare_actuator(name)
                prepared = True
                passed = 0
                for index in range(3):
                    before = self._sample(None, address, channel, cancel_event)
                    positions = []
                    for angle in (cfg.sweep_min_angle, cfg.sweep_max_angle, cfg.default_angle):
                        positions.append({"angle": angle, **self._sample(angle, address, channel, cancel_event)})
                    after = self._sample(None, address, channel, cancel_event)
                    round_result = self._evaluate_round(before, positions, after, off_max)
                    result["rounds"].append(round_result)
                    result["positions"].extend({"round": index + 1, **p} for p in positions)
                    passed += round_result["status"] == "normal"
                    if passed >= 2 or passed + 2 - index < 2:
                        break
                normal = passed >= 2
                summary = "；".join(
                    f"第{i + 1}轮{r['responsive_positions']}/3次响应，"
                    f"参考增量={r['minimum_delta_volts'] * 1000:.3f}mV，"
                    f"基线变化={r['baseline_drift_volts'] * 1000:.3f}mV"
                    for i, r in enumerate(result["rounds"]))
                result.update(status="normal" if normal else "error", passed_rounds=passed,
                    note=f"ADC 0x{address:02X}/AIN{channel} 舵机电气反馈{'通过' if normal else '未通过'}；"
                         f"{summary}；需两轮通过；未测量实际转角")
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

    def _evaluate_round(self, before, positions, after, off_max):
        cfg = self.manager.config.servo
        baseline = max(before["volts"], after["volts"])
        drift = abs(after["volts"] - before["volts"])
        # 运动采样与尖峰过滤只确认响应；不能从停止样本自动推导正常电流门槛。
        minimum = cfg.diagnostic_minimum_delta_volts
        off_ok = (abs(before["volts"]) <= off_max and abs(after["volts"]) <= off_max
                  and drift <= cfg.diagnostic_maximum_baseline_drift_volts)
        for sample in positions:
            values = sample["samples_volts"]
            filtered = [median(values[i:i + 3]) for i in range(len(values) - 2)]
            run = longest = 0
            for value in filtered:
                run = run + 1 if value - baseline >= minimum else 0
                longest = max(longest, run)
            delta = max(filtered) - baseline
            sample.update(delta_volts=delta, response_run=longest,
                          status="normal" if longest >= 3 else "error")
        responses = sum(p["status"] == "normal" for p in positions)
        return {"status": "normal" if off_ok and responses >= 2 else "error",
                "before": before, "after": after, "positions": positions,
                "minimum_delta_volts": minimum,
                "baseline_drift_volts": drift, "off_ok": off_ok,
                "responsive_positions": responses}

    @staticmethod
    def _wait(seconds, cancel_event):
        if seconds <= 0:
            return
        if cancel_event is None:
            time.sleep(seconds)
        elif cancel_event.wait(seconds):
            raise InterruptedError("检测已取消")

    def _sample(self, angle, address, channel, cancel_event):
        # 在运动开始前取得共享I2C锁，避免其他检测把0.8秒采样窗口占满。
        # 停止后的等待不占总线，其他设备仍可在各运动窗口之间采样。
        with self._motion_bus_session(angle, cancel_event):
            return self._capture_sample(angle, address, channel, cancel_event)

    @contextmanager
    def _motion_bus_session(self, angle, cancel_event):
        resource = getattr(self.manager.adc_monitor, "i2c", None)
        lock = getattr(resource, "lock", None)
        if angle is None or lock is None:
            yield
            return
        deadline = time.monotonic() + 1.0
        while True:
            self._cancel(cancel_event)
            if lock.acquire(timeout=.02):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("舵机运动采样等待I2C总线超时")
        try:
            self._cancel(cancel_event)
            yield
        finally:
            lock.release()

    def _capture_sample(self, angle, address, channel, cancel_event):
        self._cancel(cancel_event)
        if angle is None:
            self.manager.stop_actuator("servo")
        else:
            self.manager.control("servo", angle=angle)
        cfg = self.manager.config.servo
        started = time.monotonic()
        # 停止后先稳定；运动样本则从动作下发后立即开始，覆盖整个窗口。
        if angle is None:
            self._wait(cfg.diagnostic_settle_seconds, cancel_event)
        state = self.manager.actuator_state("servo")
        on = angle is not None
        driver = self._check_driver("servo", state, on, state.get("driver_error") or state.get("control_error"))
        signal = self._check_signal("servo", state, on)
        if driver["status"] != "normal" or signal["status"] != "normal":
            raise RuntimeError(driver["note"] if driver["status"] != "normal" else signal["note"])
        values, ranges_used = [], []
        full_scale = cfg.diagnostic_full_scale_volts
        count = cfg.diagnostic_sample_count if angle is None else cfg.diagnostic_motion_sample_count
        for index in range(count):
            self._cancel(cancel_event)
            if angle is not None:
                if index and time.monotonic() - started >= cfg.diagnostic_motion_window_seconds:
                    break
                target = started + index * cfg.diagnostic_motion_window_seconds / (count - 1)
                self._wait(target - time.monotonic(), cancel_event)
            volts, full_scale = self._read_feedback(address, channel, cancel_event, full_scale)
            values.append(volts)
            ranges_used.append(full_scale)
        if len(values) < cfg.diagnostic_sample_count:
            raise TimeoutError("舵机运动窗口内有效样本不足")
        return {"volts": median(values), "samples_volts": values,
                "full_scales_volts": ranges_used, "driver": driver, "signal": signal}

    def _read_feedback(self, address, channel, cancel_event, initial=None):
        # 窄量程运动尖峰饱和时扩大 PGA 量程复读，不使用截顶数值参与判断。
        initial = self.manager.config.servo.diagnostic_full_scale_volts if initial is None else initial
        ranges = [initial] + [value for value in (.512, 1.024, 2.048, 4.096, 6.144) if value > initial]
        for full_scale in ranges:
            self._cancel(cancel_event)
            sample = self.manager.adc_monitor.read_channel(channel, address=address,
                full_scale_volts=full_scale)
            volts = sample.get("volts")
            if isinstance(volts, bool) or not isinstance(volts, (int, float)) or not math.isfinite(volts):
                raise ValueError("舵机反馈电压无效")
            if not sample.get("saturated"):
                return volts, full_scale
        raise ValueError("舵机反馈在最大 ADC 量程下仍饱和")

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
