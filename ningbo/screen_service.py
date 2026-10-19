"""Serial connection lifecycle and scene message routing."""
from hardware.screen.frames import ScreenEventParser
from hardware.screen.sender import ScreenSender
import json
from importlib import import_module
from config import DEFAULT_CONFIG
from experiments import EXPERIMENTS
from system.diagnostics.serial_screen import screen as _diagnostics


_FORMATTERS = {name: import_module(f"experiments.{name}.serial_screen.screen") for name in EXPERIMENTS}
_FORMATTERS["diagnostics"] = _diagnostics
SCENE_ALIASES = {"body_temp": "body_temperature", "body": "body_temperature", "smoke_alarm": "smoke"}

class MessageParser:
    def __init__(self, voice_config=None):
        self.voice_config = voice_config or DEFAULT_CONFIG.voice

    def parse(self, message):
        data = self._to_dict(message)
        scene = data.get("scene")
        if not isinstance(scene, str) or not scene:
            raise ValueError("消息缺少有效的 scene 字段")
        scene = SCENE_ALIASES.get(scene, scene)
        formatter = _FORMATTERS.get(scene)
        if formatter is None:
            raise ValueError("暂不支持的场景: " + scene)
        return formatter.build_updates(data, self.voice_config)


    @staticmethod
    def _to_dict(message):
        """把 JSON 字符串或 dict 统一转换成 dict。"""

        if isinstance(message, dict):
            return message

        if isinstance(message, str):
            try:
                data = json.loads(message)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON 格式错误: {exc}") from exc

            if not isinstance(data, dict):
                raise ValueError("JSON 顶层必须是对象")

            return data

        raise TypeError("message 只支持 dict 或 JSON 字符串")


class ScreenService:
    def __init__(self, port=None, baudrate=None, config=None, voice_config=None, excluded_ports=()):
        self.parser = MessageParser(voice_config=voice_config)
        self.sender = ScreenSender(port, baudrate, config=config, excluded_ports=excluded_ports)
        self.event_parser = ScreenEventParser()

    def start(self):
        """启动串口屏服务。"""
        self.sender.open()

    def stop(self):
        """停止串口屏服务。"""
        self.sender.close()

    def discard_startup_events(self):
        """就绪前收到的上电/探测遗留帧不能作为用户点击执行。"""
        self.sender.take_pending_input()
        if self.sender.ser is not None and self.sender.ser.is_open:
            self.sender.ser.reset_input_buffer()
        self.event_parser = ScreenEventParser()


    def handle_message(self, message):
        """Format in the owning scene, then write through the single serial link."""
        return self.sender.send_updates(self.parser.parse(message))

    def read_screen_events(self):
        """读取当前已到达的屏幕按钮/变量回传，返回标准事件列表。

        调用方可按 ``event`` 字段执行实际业务动作，例如收到
        ``environment.start`` 后启动环境传感器采集。
        """
        if self.sender.ser is None or not self.sender.ser.is_open:
            raise RuntimeError("串口屏服务尚未启动")
        available = self.sender.ser.in_waiting
        pending = self.sender.take_pending_input()
        if available:
            pending += self.sender.ser.read(available)
        return self.event_parser.feed(pending) if pending else []
