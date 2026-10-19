"""串口屏采集/控制按钮与设备业务桥接；状态检测交给独立桥接模块。"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional
from config import DEFAULT_CONFIG
from hardware.rgb.feedback import RgbFeedback
from experiments.ultrasonic.jetson.alarm import ProximityAlarm
from experiments.access.bridge import AccessScreenBridge
from experiments.voice.bridge import VoiceScreenBridge
from system.diagnostics.bridge import DiagnosticsScreenBridge

from experiments.environment.bridge import EnvironmentBridge
from experiments.body_temperature.bridge import BodyTemperatureBridge
from experiments.ultrasonic.bridge import UltrasonicBridge
from experiments.smoke.bridge import SmokeBridge

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
        self.feedback = RgbFeedback(devices, self.config, wall_clock=wall_clock)
        self.proximity_alarm = ProximityAlarm(devices, self.config, wall_clock=wall_clock)
        self.access = AccessScreenBridge(self.feedback, access_handler, access_service,
                                         devices, self.config.face, self.clock)
        self.controls = ControlScreenBridge(devices, self.config, screen=screen)
        self.voice = VoiceScreenBridge(screen, service=voice_service, handler=voice_handler)
        self.diagnostics = DiagnosticsScreenBridge(screen, devices)
        self.sensor_scenes = {
            'environment': EnvironmentBridge(self),
            'body_temperature': BodyTemperatureBridge(self),
            'ultrasonic': UltrasonicBridge(self),
            'smoke': SmokeBridge(self),
        }
        self.scene_bridges = {**self.sensor_scenes, "fan": self.controls.fan,
                              "servo": self.controls.servo, "stepper": self.controls.stepper,
                              "access": self.access, "voice": self.voice}

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
        """Route each event to its owning experiment."""
        name = event["event"] if isinstance(event, dict) else event
        if name.startswith("diagnostics."):
            return self.diagnostics.handle_event(name)
        scene, _, _ = name.rpartition(".")
        bridge = self.scene_bridges.get(scene)
        if bridge is not None:
            try:
                response = bridge.handle_event(name if scene in ("access", "voice") else event)
            finally:
                if scene == "access":
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
            LOGGER.exception("电机状态写屏失败")
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
        for scene in tuple(self.active_scenes):
            sent.extend(self.sensor_scenes[scene].tick(snapshot, now))
        return sent

    def _scene_values(self, scene, snapshot):
        """Retained API; data interpretation is implemented in the owning scene."""
        bridge = self.sensor_scenes.get(scene)
        if bridge is None:
            raise ValueError(f"Unsupported scene: {scene}")
        return bridge.values(snapshot)


from experiments.fan.bridge import FanScreenBridge
from experiments.servo.bridge import ServoScreenBridge
from experiments.stepper.bridge import StepperScreenBridge


class ControlScreenBridge:
    PARAMETER_NAMES = StepperScreenBridge.PARAMETER_NAMES

    def __init__(self, devices, config, screen=None):
        self.fan = FanScreenBridge(devices, config, screen)
        self.servo = ServoScreenBridge(devices, config, screen)
        self.stepper = StepperScreenBridge(devices, config, screen)
        self.bridges = (self.servo, self.stepper, self.fan)
        self.parameters = self.stepper.parameters

    def handle_event(self, event):
        for bridge in self.bridges:
            result = bridge.handle_event(event)
            if result is not None:
                return result
        return None

    def reset(self):
        self.servo.reset()
        self.stepper.reset()

    def tick(self, snapshot, now):
        sent = []
        for bridge in (self.servo, self.stepper):
            try:
                sent.extend(bridge.tick(snapshot, now))
            except Exception:
                LOGGER.exception("%s 状态写屏失败，下次循环重试", type(bridge).__name__)
        return sent
