"""
service.py

作用：
把“接收业务消息 -> 解析消息 -> 发送到串口屏”串起来。

本文件不负责具体场景逻辑，也不关心 JGUS 帧细节。
它只负责调度：
    message_parser.py -> screen_sender.py
"""

from Serial_Screen.screen_service.screen.message_parser import MessageParser
from Serial_Screen.screen_service.screen.screen_events import ScreenEventParser
from Serial_Screen.screen_service.screen.screen_sender import ScreenSender


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

    def handle_message(self, message):
        """
        接收一条业务消息并更新串口屏。

        message 可以是 dict：
        {
            "scene": "environment",
            "temperature": 25.36,
            "humidity": 63.20,
            "light": 428.00
        }

        也可以是 JSON 字符串。
        """

        # 1. 解析业务消息
        updates = self.parser.parse(message)

        # 2. 转成 JGUS 数据帧并发送
        sent = self.sender.send_updates(updates)

        # 3. 返回发送结果，方便日志和调试
        return sent

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

