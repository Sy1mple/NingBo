"""串口屏语音按钮、USB 识别服务及文本 VP 回写。"""
import logging

LOGGER = logging.getLogger(__name__)


class VoiceScreenBridge:
    def __init__(self, screen, service=None, handler=None):
        self.screen = screen
        self.service = service
        self.handler = handler
        self.active = False
        self.session_id = None
        self._pending = None
        self._last_text = None
        self._last_error = ""
        self._has_text = False
        self._showing_notice = False

    def handle_event(self, name):
        if name not in ("voice.start", "voice.stop"):
            return None
        if name == "voice.stop":
            result, sent = self._stop(clear_text=True)
            return {"event": name, "handled": True, "result": result, "sent": sent}
        if self.service is None and self.handler is None:
            return {"event": name, "handled": False, "sent": [], "note": "语音识别服务尚未接入"}
        if self.active:
            return {"event": name, "handled": False, "sent": [], "note": "语音任务已启动"}
        result = self.service.start() if self.service is not None else self.handler(name)
        if isinstance(result, dict) and not result.get("accepted", True):
            return {"event": name, "handled": False, "sent": [], "result": result, "note": result.get("note", "语音启动失败")}
        self.active = True
        self.session_id = result.get("session_id") if isinstance(result, dict) else None
        self._pending = {"text": "正在启动语音"}
        self._has_text = False
        self._showing_notice = True
        self._last_error = ""
        self._last_text = None
        sent = self._flush_pending()
        return {"event": name, "handled": True, "sent": sent, "result": result,
                "note": "语音任务已启动，等待识别文本"}

    def _stop(self, clear_text):
        was_active = self.active
        self.active = False
        self.session_id = None
        self._pending = None
        self._last_text = None
        result, sent = None, []
        try:
            if self.service is not None:
                result = self.service.stop()
            elif self.handler is not None and was_active:
                result = self.handler("voice.stop")
        finally:
            if clear_text:
                sent = self.screen.handle_message({"scene": "voice", "recognized_text": ""})
        return result, sent

    def tick(self):
        if self.service is None:
            return []
        if not self.active:
            return self._flush_pending()
        state = self.service.snapshot()
        if state.get("phase") == "error":
            error = state.get("error", "语音任务失败")
            if error != self._last_error:
                LOGGER.error("USB 语音识别失败：%s", error)
                self._last_error = error
            self.active = False
            self._pending = {"text": "语音已停止：" + error}
            return self._flush_pending()
        if state.get('phase') == 'idle' and not state.get('worker_alive', True):
            self.active = False
            self._pending = {"text": "语音已停止，请重新点开始"}
            return self._flush_pending()
        for result in self.service.poll_results():
            if result.get("session_id") == self.session_id:
                self._pending = result
                self._has_text = True
                self._showing_notice = False
        notice = state.get('notice', '')
        if self._pending is None and notice and (not self._has_text or self._showing_notice
                                                or state.get('engine_phase') == 'recovering'):
            self._pending = {'text': notice}
            self._showing_notice = True
        return self._flush_pending()

    def _flush_pending(self):
        if self._pending is None:
            return []
        text = self._pending["text"]
        if text == self._last_text:
            self._pending = None
            return []
        # 串口写成功后才记为已发送；失败时保留文本，在下一次 tick 重试。
        try:
            sent = self.screen.handle_message({"scene": "voice", "recognized_text": text})
        except ValueError as exc:
            # 字库/协议参数错误不会通过重试恢复；保留上一条有效显示，等待新结果。
            self._pending = None
            LOGGER.error("语音文字无法写屏，本条结果已跳过：%s", exc)
            return []
        LOGGER.info("语音文字已写屏：VP=0x1064，%d 字", len(text))
        self._last_text = text
        self._pending = None
        return sent

    def close(self):
        self._stop(clear_text=False)

    def reset(self):
        self.active = False
        self.session_id = None
        self._pending = None
        self._last_text = None
        self._last_error = ""
        self._has_text = False
        self._showing_notice = False
