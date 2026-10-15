"""串口屏采集/控制按钮与设备业务桥接；状态检测交给独立桥接模块。"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional
from JETSON.Sensor.Config import DEFAULT_CONFIG
from business.rgb_feedback import RgbFeedback, valid_number
from business.proximity_alarm import ProximityAlarm
from Serial_Screen.access_bridge import AccessScreenBridge
from Serial_Screen.control_bridge import ControlScreenBridge
from Serial_Screen.voice_bridge import VoiceScreenBridge
from Serial_Screen.diagnostics_bridge import DiagnosticsScreenBridge

LOGGER = logging.getLogger(__name__)
SCENES = {"environment", "body_temperature", "ultrasonic", "smoke"}


class ScreenDeviceBridge:
    def __init__(
        self,
        screen,
        devices,
        interval: float = 1.0,
        light_full_scale_lux: float | None = None,
        clock: Optional[Callable[[], float]] = None,
        access_handler=None,
        config=None,
        voice_handler=None,
        wall_clock=None,
        voice_service=None,
        access_service=None,
    ):
        """Create a bridge, without claiming physical calibration.

        `light_full_scale_lux` is an EXAMPLE display normalization:
            displayed_percent = clamp(100 * measured_lux / full_scale_lux, 0, 100)
        Lux is an absolute unit; this number is not a calibrated lux percentage.
        """
        self.config = config or getattr(devices, "config", DEFAULT_CONFIG)
        if light_full_scale_lux is None:
            light_full_scale_lux = self.config.light.display_full_scale_lux
        if interval <= 0 or light_full_scale_lux <= 0:
            raise ValueError("interval and light_full_scale_lux must be positive")
        self.screen = screen
        self.devices = devices
        self.interval = interval
        self.light_full_scale_lux = light_full_scale_lux
        self.clock = clock or time.monotonic
        self.active_scenes = {}
        self._smoke_display_overflow = False
        self.feedback = RgbFeedback(devices, self.config, wall_clock=wall_clock)
        self.proximity_alarm = ProximityAlarm(devices, self.config, wall_clock=wall_clock)
        self.access = AccessScreenBridge(self.feedback, access_handler, access_service,
                                         devices, self.config.face, self.clock)
        self.controls = ControlScreenBridge(devices, self.config, screen=screen)
        self.voice = VoiceScreenBridge(screen, service=voice_service, handler=voice_handler)
        self.diagnostics = DiagnosticsScreenBridge(screen, devices)

    @property
    def access_enabled(self):
        return self.access.enabled

    def report_access_result(self, recognized, session_id):
        return self.access.report_result(recognized, session_id)

    def reset(self):
        self.active_scenes.clear()
        self.access.reset()
        self.controls.reset()
        self.voice.reset()
        self.feedback.reset()
        self.proximity_alarm.reset()

    def stop(self):
        """通知已接入的识别服务停止；执行器资源由 application 统一关闭。"""
        errors = []
        try:
            try:
                self.access.close()
            except Exception as exc:
                errors.append(str(exc))
            try:
                self.voice.close()
            except Exception as exc:
                errors.append(str(exc))
            try:
                self.proximity_alarm.release()
                self.proximity_alarm.flush()
            except Exception as exc:
                errors.append(str(exc))
        finally:
            self.reset()
        if errors:
            raise RuntimeError("识别服务停止失败: " + "; ".join(errors))

    def _flush_feedback(self, snapshot=None):
        try:
            self.feedback.flush(snapshot)
        except Exception:
            LOGGER.exception("RGB 联动输出失败，将在下次循环重试")

    def handle_event(self, event):
        """Accept an event dict from ScreenEventParser or an event name string."""
        name = event["event"] if isinstance(event, dict) else event
        response = self.voice.handle_event(name)
        if response is not None:
            return response
        if name.startswith("diagnostics."):
            return self.diagnostics.handle_event(name)
        scene, dot, action = name.rpartition(".")
        if scene in SCENES and action in ("start", "stop"):
            if action == "start":
                self.active_scenes[scene] = self.clock()
                self.feedback.activate(scene)
                if scene == "ultrasonic":
                    self.proximity_alarm.activate()
                sent = self.tick()
                state = self.devices.snapshot().get("sensors", {}).get("body_temperature") or {}
                unsupported = scene == "body_temperature" and (not state or state.get("status") in ("unconfigured", "disabled"))
                return {"event": name, "handled": True, "sent": sent,
                        "note": "人体温度真实传感器尚未接入" if unsupported else "采集已启动"}
            self.active_scenes.pop(scene, None)
            self.feedback.release(scene)
            if scene == "ultrasonic":
                self.proximity_alarm.release()
                self._flush_proximity_alarm()
            self._flush_feedback()
            zeros = {
                "environment": {"temperature": 0, "humidity": 0, "light": 0},
                "body_temperature": {"temperature": 0},
                "ultrasonic": {"distance": 0},
                "smoke": {"value": 0},
            }[scene]
            sent = self.screen.handle_message({"scene": scene, **zeros})
            return {"event": name, "handled": True, "sent": sent, "note": "采集已停止"}

        response = self.controls.handle_event(event)
        if response is not None:
            return response
        try:
            response = self.access.handle_event(name)
        finally:
            if name.startswith("access."):
                self.access.tick()
                self._flush_feedback()
        if response is not None:
            return response

        return {"event": name, "handled": False, "sent": [],
                "note": "未定义的屏幕操作，无硬件动作"}

    def _flush_proximity_alarm(self, snapshot=None):
        try:
            self.proximity_alarm.flush(snapshot)
        except Exception:
            LOGGER.exception("测距蜂鸣器输出失败，将在下次循环重试")

    def tick(self):
        """Send updated values only for scenes started on the actual HMI."""
        sent = []
        now = self.clock()
        snapshot = self.devices.snapshot()
        try:
            sent.extend(self.controls.tick(snapshot, now))
        except Exception:
            LOGGER.exception("舵机角度写屏失败")
        self.feedback.observe(snapshot.get("sensors", {}))
        self.proximity_alarm.observe(snapshot.get("sensors", {}))
        self._flush_proximity_alarm(snapshot)
        self.access.tick()
        self._flush_feedback(snapshot)
        try:
            sent.extend(self.voice.tick())
        except Exception:
            LOGGER.exception("语音文本写屏失败")
        try:
            sent.extend(self.diagnostics.tick())
        except Exception:
            LOGGER.exception("状态检测写屏失败")
        for scene, due in tuple(self.active_scenes.items()):
            if now < due:
                continue
            values = self._scene_values(scene, snapshot)
            if values:
                sent.extend(self.screen.handle_message({"scene": scene, **values}))
            self.active_scenes[scene] = now + self.interval
        return sent

    def _scene_values(self, scene, snapshot):
        """Translate backend fields without inventing unknown sensor readings."""
        sensors = snapshot.get("sensors", {})
        if scene == "environment":
            out = {}
            dht = sensors.get("dht11") or {}
            light = sensors.get("bh1750") or {}
            if dht.get("temp") is not None:
                out["temperature"] = dht["temp"]
            if dht.get("humidity") is not None:
                out["humidity"] = dht["humidity"]
            if light.get("lux") is not None:
                out["light"] = max(0, min(100, round(light["lux"] * 100 / self.light_full_scale_lux)))
            return out
        if scene == "ultrasonic":
            distance = valid_number(sensors.get("ultrasonic") or {}, "distance", self.feedback.wall_clock(),
                                    self.config.rgb_feedback.sample_max_age_seconds, 0,
                                    self.config.ultrasonic.maximum_distance_cm)
            return {"distance": distance} if distance is not None else {}
        if scene == "smoke":
            smoke = sensors.get("smoke") or {}
            if smoke.get("protocol") == "ffaa":
                value = valid_number(smoke, "concentration", self.feedback.wall_clock(),
                                     self.config.smoke.stale_after_seconds, 0, 0xFFFF)
                if value is None:
                    return {}
                if value > 0x7FFF:
                    if not self._smoke_display_overflow:
                        LOGGER.warning("烟雾浓度超过当前屏幕有符号 Word 显示范围，未截断数值")
                    self._smoke_display_overflow = True
                    return {}
                self._smoke_display_overflow = False
                return {"value": value}
            # Actual UART payload is only raw bytes. Only send a percent after a
            # separate sensor-protocol parser + calibration provides it.
            percent = smoke.get("percent")
            return {"value": max(0, min(100, round(percent)))} if percent is not None else {}
        if scene == "body_temperature":
            low, high = self.config.body_temperature.display_range
            temperature = valid_number(sensors.get("body_temperature") or {}, "temperature", self.feedback.wall_clock(),
                                       self.config.rgb_feedback.sample_max_age_seconds, low, high)
            return {"temperature": temperature} if temperature is not None else {}
        raise ValueError(f"Unsupported scene: {scene}")
