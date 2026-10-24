"""步进场景：相序线程状态及电气反馈检查。"""
import math
from statistics import median
import time
from hardware.diagnostics import ThreadedActuatorCheck


class StepperStatusCheck(ThreadedActuatorCheck):
    """步进通过运行线程和反馈判断，不对动态相序做静态电平比较。"""

    def check(self, name, cancel_event=None):
        if name != "stepper":
            raise ValueError("步进检测设备名称不匹配")
        with self.control_session(name, cancel_event):
            adc = self.manager.config.ads1115
            if (getattr(self.manager, "_pintest_mode", False)
                    or name not in self.manager._outputs or not adc.enabled
                    or name not in adc.feedback_channels or name not in adc.feedback_voltage_limits):
                return super().check(name, cancel_event)
            cfg = self.manager.config.stepper
            address = adc.feedback_addresses.get(name, adc.address)
            channel = adc.feedback_channels[name]
            off_max, _ = adc.feedback_voltage_limits[name]
            result = {"status": "error", "transport": "i2c_ads1115",
                      "check_scope": "electrical_response", "address": hex(address), "channel": channel,
                      "maximum_off_absolute_volts": off_max,
                      "minimum_delta_volts": cfg.diagnostic_minimum_delta_volts,
                      "runs": [], "diagnostic_steps": cfg.diagnostic_steps,
                      "diagnostic_speed_ms": cfg.diagnostic_speed_ms,
                      "calibration_basis": "20261024_user_confirmed_bidirectional_motion; disconnected_load_not_verified"}
            touched = False
            try:
                self._cancel(cancel_event)
                previous = self.manager.actuator_state(name)
                result["cmd_on"] = bool(previous.get("running"))
                # Observe an existing job without resetting its remaining steps.
                if previous.get("running"):
                    sample = self._running_sample(address, channel, cancel_event)
                    if not sample.get("completed"):
                        normal = sample["volts"] >= cfg.diagnostic_minimum_delta_volts
                        result.update(status="normal" if normal else "error", mode="current_run",
                            runs=[sample], note=f"ADC 0x{address:02X}/AIN{channel} 当前步进运行反馈"
                            f"{'通过' if normal else '不足'}；{sample['volts'] * 1000:.3f}mV；保持原步数任务")
                        self.manager.record_actuator_diagnostic(name, result)
                        return result
                    # The previous job finished naturally during observation.
                    # Its completed steps must not be restored or repeated.
                self.manager.prepare_actuator(name)
                touched = True
                result["mode"] = "idle_bidirectional_test"
                stops = [self._stopped_sample(address, channel, cancel_event)]
                for direction in ("fwd", "rev"):
                    self._cancel(cancel_event)
                    started = time.monotonic()
                    self.manager.control(name, dir_=direction, n=cfg.diagnostic_steps, spd=cfg.diagnostic_speed_ms)
                    sample = self._running_sample(address, channel, cancel_event)
                    if sample.get("completed"):
                        raise RuntimeError("步进检测动作在采样完成前结束，请复测")
                    result["runs"].append(sample)
                    # 采样结束后继续执行剩余步数，不把可观察动作缩成短闪。
                    sample.update(self._finish_direction(direction, started, cancel_event))
                    stops.append(self._stopped_sample(address, channel, cancel_event))
                result["stops"] = stops
                baseline = max(s["volts"] for s in stops)
                drift = max(s["volts"] for s in stops) - min(s["volts"] for s in stops)
                off_ok = (all(abs(s["volts"]) <= off_max for s in stops)
                          and drift <= cfg.diagnostic_maximum_baseline_drift_volts)
                for sample in result["runs"]:
                    delta = sample["volts"] - baseline
                    values = sample["samples_volts"]
                    responses = sum(value - baseline >= cfg.diagnostic_minimum_delta_volts for value in values)
                    sample.update(delta_volts=delta, response_samples=responses,
                                  status="normal" if responses >= math.ceil(len(values) * .75) else "error")
                normal = off_ok and all(s["status"] == "normal" for s in result["runs"])
                summary = "，".join(f"{s['direction']}={s['volts'] * 1000:.3f}mV"
                                   f"(完整{cfg.diagnostic_steps}步/{s['duration_seconds']:.1f}秒)" for s in result["runs"])
                result.update(status="normal" if normal else "error", baseline_drift_volts=drift,
                    note=f"ADC 0x{address:02X}/AIN{channel} 步进正反转电气反馈{'通过' if normal else '未通过'}；"
                         f"停止={baseline * 1000:.3f}mV；{summary}；停止四相已读回低电平；未测量实际转角或丢步")
            except InterruptedError:
                result.update(status="unknown", note="步进状态检测已取消")
            except Exception as exc:
                result.update(status="error", note=f"步进状态检测失败: {exc}")
            finally:
                if touched:
                    try:
                        self.manager.stop_actuator(name)
                        result["final_levels"] = self._off_levels()
                        result["restored_mode"] = "off"
                    except Exception as exc:
                        result.update(status="error", note=f"步进检测后停止失败: {exc}")
            self.manager.record_actuator_diagnostic(name, result)
            return result

    @staticmethod
    def _cancel(event):
        if event is not None and event.is_set():
            raise InterruptedError("检测已取消")

    def _wait(self, event):
        self._cancel(event)
        seconds = self.manager.config.stepper.diagnostic_settle_seconds
        if event is None:
            time.sleep(seconds)
        elif event.wait(seconds):
            raise InterruptedError("检测已取消")

    def _off_levels(self):
        levels = self.manager.actuator_output_levels("stepper")
        if levels != {pin: 0 for pin in self.manager.config.stepper.pins}:
            raise RuntimeError("停止后的步进四相未全部回到低电平")
        return levels

    def _finish_direction(self, direction, started, event):
        cfg = self.manager.config.stepper
        deadline = started + cfg.diagnostic_steps * cfg.diagnostic_speed_ms / 1000 * 1.5 + 1
        previous = None
        last_progress = time.monotonic()
        while True:
            self._cancel(event)
            state = self.manager.actuator_state("stepper")
            self._driver_ok(state)
            now = time.monotonic()
            if not state.get("running"):
                if state.get("n") != 0:
                    raise RuntimeError("步进检测动作未完成全部步数")
                return {"completed_steps": cfg.diagnostic_steps, "duration_seconds": now - started}
            if state.get("dir") != direction:
                raise RuntimeError("步进检测过程中方向意外改变")
            remaining = state.get("n")
            if not isinstance(remaining, int) or remaining < 0:
                raise RuntimeError("步进剩余步数无效")
            if previous is not None and remaining > previous:
                raise RuntimeError("步进剩余步数意外增加")
            if previous is None or remaining < previous:
                last_progress = now
            elif now - last_progress >= .5:
                raise RuntimeError("步进相序线程持续未推进剩余步数")
            if now >= deadline:
                raise TimeoutError("步进检测完整动作等待超时")
            previous = remaining
            if event is None:
                time.sleep(.05)
            elif event.wait(.05):
                raise InterruptedError("检测已取消")

    def _stopped_sample(self, address, channel, event):
        self._cancel(event)
        self.manager.stop_actuator("stepper")
        self._wait(event)
        return {**self._voltage(address, channel, event), "levels": self._off_levels()}

    def _running_sample(self, address, channel, event):
        self._wait(event)
        before = self.manager.actuator_state("stepper")
        self._driver_ok(before)
        if not before.get("running"):
            return {"completed": True}
        values = self._voltage(address, channel, event)
        after = self.manager.actuator_state("stepper")
        self._driver_ok(after)
        if not after.get("running") and after.get("n") == 0:
            return {"completed": True, **values}
        if after.get("dir") != before.get("dir") or after.get("n", 0) >= before.get("n", 0):
            raise RuntimeError("步进相序线程未推进剩余步数")
        return {**values, "direction": before["dir"], "before": before, "after": after,
                "progressed_steps": before["n"] - after["n"]}

    @staticmethod
    def _driver_ok(state):
        if state.get("driver_error") or state.get("control_error"):
            raise RuntimeError(state.get("driver_error") or state["control_error"])
        if not state.get("worker_alive"):
            raise RuntimeError("步进相序线程未运行")

    def _voltage(self, address, channel, event):
        cfg = self.manager.config.stepper
        samples = []
        for _ in range(cfg.diagnostic_sample_count):
            self._cancel(event)
            sample = self.manager.adc_monitor.read_channel(channel, address=address,
                full_scale_volts=cfg.diagnostic_full_scale_volts)
            volts = sample.get("volts")
            if (sample.get("saturated") or isinstance(volts, bool)
                    or not isinstance(volts, (int, float)) or not math.isfinite(volts)):
                raise ValueError("步进反馈电压无效或超出 ADC 量程")
            samples.append(volts)
        return {"volts": median(samples), "samples_volts": samples}
