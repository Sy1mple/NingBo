"""工程编排：串口屏与设备共用一套生命周期和业务桥接。"""
import logging
import time
import threading

from device_service import DEFAULT_CONFIG, DeviceService
from experiments.voice.jetson.service import VoiceService
from experiments.access.jetson.service import FaceService
from scene_router import ScreenDeviceBridge
from screen_service import ScreenService

LOGGER = logging.getLogger("ningbo")

class NingBoApplication:
    def __init__(self, config=None, devices=None, screen=None, access_handler=None, voice_handler=None, voice_service=None,
                 access_service=None, camera_preview=None):
        self.config = (config or DEFAULT_CONFIG).validate()
        self.devices = devices or DeviceService(config=self.config)
        self.screen = screen or ScreenService(config=self.config.screen, voice_config=self.config.voice,
                                             excluded_ports=(self.config.smoke.port,) if self.config.smoke.enabled else ())
        self.voice = voice_service if voice_service is not None else (
            VoiceService(self.config.voice, self.config.thread_join_timeout) if voice_handler is None else None
        )
        self.camera_preview = camera_preview
        if (self.camera_preview is None and self.config.face.enabled and self.config.camera.preview_enabled
                and access_handler is None and access_service is None):
            from experiments.access.jetson.preview import CameraPreview
            self.camera_preview = CameraPreview(port=self.config.camera.preview_port,
                                                open_browser=self.config.camera.preview_local_browser)
        self.face = access_service if access_service is not None else (
            FaceService(self.config.face, self.config.camera, self.config.thread_join_timeout,
                        preview=self.camera_preview)
            if access_handler is None else None
        )
        self.bridge = ScreenDeviceBridge(
            self.screen, self.devices,
            interval=self.config.screen.refresh_interval,
            light_full_scale_lux=self.config.light.display_full_scale_lux,
            access_handler=access_handler,
            voice_handler=voice_handler,
            config=self.config,
            voice_service=self.voice,
            access_service=self.face,
        )
        self._started = False
        self._shutdown = threading.Event()

    def start(self):
        if self._started:
            return
        self.bridge.reset()
        try:
            self.screen.start()
            self.devices.start()
            if self.camera_preview is not None:
                self.camera_preview.start(launch_browser=False)
            discard = getattr(self.screen, 'discard_startup_events', None)
            if discard is not None:
                discard()
            self._started = True
        except Exception:
            try:
                self.stop()
            except Exception:
                LOGGER.exception("启动失败后的资源清理异常")
            raise
        LOGGER.info("工程已启动，串口屏 %s；等待开始/停止按钮", self.config.screen.port)

    def process_once(self):
        """一次按钮处理和显示刷新，所有结果来自设备服务。"""
        responses = []
        for event in self.screen.read_screen_events():
            try:
                response = self.bridge.handle_event(event)
                responses.append(response)
                if response.get("handled"):
                    LOGGER.info("按钮 %s：%s", response["event"], response.get("note", response.get("result", "已处理")))
                else:
                    LOGGER.warning("按钮 %s：%s", response["event"], response.get("note", "未接入"))
            except Exception:
                LOGGER.exception("按钮处理失败：%s", event.get("event"))
        try:
            self.bridge.tick()
        except Exception:
            LOGGER.exception("设备数据刷新或写屏失败")
        return responses

    def run(self):
        if not self._started:
            raise RuntimeError("请先启动工程")
        while not self._shutdown.is_set():
            self.process_once()
            self._shutdown.wait(self.config.screen.event_poll_interval)

    def request_stop(self):
        """信号处理只请求退出；主线程按正常生命周期释放 PWM 和外设。"""
        self._shutdown.set()

    def report_access_result(self, recognized, session_id):
        """真实人脸识别服务的回调入口，主循环随后更新 RGB。"""
        return self.bridge.report_access_result(recognized, session_id)

    def stop(self):
        try:
            self.bridge.stop()
        except Exception:
            LOGGER.exception("业务桥接停止失败")
        try:
            result = self.devices.emergency_stop()
            if not result.get("ok", True):
                LOGGER.error("部分执行器停止失败：%s", result.get("errors"))
        finally:
            try:
                self.devices.stop()
            finally:
                try:
                    self.screen.stop()
                finally:
                    if self.camera_preview is not None:
                        self.camera_preview.stop()
                    self._started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stop()
        return False
