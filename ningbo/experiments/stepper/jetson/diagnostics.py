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
            off_max, on_min = adc.feedback_voltage_limits[name]
            result = {"status": "error", "transport": "i2c_ads1115",
                      "check_scope": "electrical_response", "address": hex(address), "channel": channel,
                      "maximum_off_absolute_volts": off_max, "minimum_run_volts": on_min,
                      "minimum_delta_volts": cfg.diagnostic_minimum_delta_volts,
                      "runs": [], "calibration_basis": "normal_load_samples; disconnected_load_not_verified"}
            touched = False
            try:
                self._cancel(cancel_event)
                previous = self.manager.actuator_state(name)
                result["cmd_on"] = bool(previous.get("running"))
                # Observe an existing job without resetting its remaining steps.
                if previous.get("running"):
                    sample = self._running_sample(address, channel, cancel_event)
                    if not sample.get("completed"):
                        normal = sample["volts"] >= on_min
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
                    self.manager.control(name, dir_=direction, n=cfg.diagnostic_steps, spd=cfg.default_speed_ms)
                    sample = self._running_sample(address, channel, cancel_event)
                    if sample.get("completed"):
                        raise RuntimeError("步进检测动作在采样完成前结束，请复测")
                    result["runs"].append(sample)
                    stops.append(self._stopped_sample(address, channel, cancel_event))
                result["stops"] = stops
                baseline = median(s["volts"] for s in stops)
                drift = max(s["volts"] for s in stops) - min(s["volts"] for s in stops)
                off_ok = (all(abs(s["volts"]) <= off_max for s in stops)
                          and drift <= cfg.diagnostic_maximum_baseline_drift_volts)
                for sample in result["runs"]:
                    delta = sample["volts"] - baseline
                    sample.update(delta_volts=delta, status="normal" if sample["volts"] >= on_min
                                  and delta >= cfg.diagnostic_minimum_delta_volts else "error")
                normal = off_ok and all(s["status"] == "normal" for s in result["runs"])
                summary = "，".join(f"{s['direction']}={s['volts'] * 1000:.3f}mV" for s in result["runs"])
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
