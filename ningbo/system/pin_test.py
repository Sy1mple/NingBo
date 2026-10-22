"""Explicit board pin testing. Normal experiment control stays in its own scene."""


class PinTest:
    def __init__(self, manager):
        self.manager = manager

    def pintest_on(self):
        manager = self.manager
        if not manager._started:
            raise RuntimeError("设备服务尚未启动")
        with manager._control_lock:
            if manager._diagnostic_owners:
                return {"mode": "blocked", "ok": False, "error": "状态检测尚未结束，无法进入引脚测试"}
            # 引脚测试本身是显式维护操作；进入时才准备执行器以便退出/急停能恢复关闭电平。
            if not manager._pintest_mode:
                for name, scene in manager._output_owners.items():
                    if name in scene._outputs:
                        manager.prepare_actuator(name)
            result = manager.emergency_stop()
            if not result["ok"]:
                return result
            with manager._sampling_condition:
                manager._pintest_mode = True
                if not manager._sampling_condition.wait_for(lambda: manager._active_reads == 0, manager.config.thread_join_timeout):
                    return {"mode": "blocked", "ok": False, "error": "采集尚未结束，无法进入引脚测试"}
            return {"mode": "on", "ok": True}

    def pintest_off(self):
        manager = self.manager
        with manager._control_lock:
            result = manager.emergency_stop(resume_sampling=False)
            if not result["ok"]:
                return result
            if manager._started:
                for driver in manager._outputs.values():
                    if hasattr(driver, "start") and not hasattr(driver, "_thread"):
                        driver.start()
                if manager.config.ultrasonic.enabled:
                    manager._readers["ultrasonic"].start()
            manager._pintest_mode = False
            return {"mode": "off", "ok": True}

    def pintest_set(self, pin, level):
        manager = self.manager
        with manager._control_lock:
            pin, level = int(pin), int(bool(int(level)))
            if not manager._pintest_mode:
                return {"error": "请先打开引脚测试模式", "pin": pin}
            if pin not in manager.pintest_out:
                return {"error": "该脚不是允许测试的输出脚", "pin": pin}
            try:
                manager.gpio.setup_output(pin, level)
                manager._pintest_levels[pin] = level
                return {"pin": pin, "level": level, "name": manager.pintest_out[pin]}
            except Exception as exc:
                return {"error": str(exc), "pin": pin}

    def pintest_status(self):
        manager = self.manager
        pins = [{"pin": pin, "name": name, "dir": "out", "level": manager._pintest_levels.get(pin, 0)} for pin, name in manager.pintest_out.items()]
        pins += [{"pin": pin, "name": name, "dir": "in", "level": None} for pin, name in manager.pintest_in.items()]
        return {"mode": "on" if manager._pintest_mode else "off", "pins": sorted(pins, key=lambda item: item["pin"]), "note": "I2C/UART 引脚不做输出测试"}

    def reset_other_outputs(self):
        manager = self.manager
        errors = {}
        if manager._started and manager._pintest_mode:
            actuator_pins = {getattr(getattr(manager.config, name), "pin", None)
                             for name in ("buzzer", "fan", "lock", "servo")}
            actuator_pins.update(manager.config.rgb.pins + manager.config.stepper.pins)
            for pin in set(manager.pintest_out) - actuator_pins:
                try:
                    manager.gpio.setup_output(pin, 0)
                except Exception as exc:
                    errors[f"pin-{pin}"] = str(exc)
        manager._pintest_levels.clear()
        return errors
