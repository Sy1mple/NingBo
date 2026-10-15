"""门禁按钮和真实识别结果适配；会话标识阻止过期回调改变指示灯。"""
import queue
import threading
import logging
import time

from business.access_control import AccessLockControl

LOGGER = logging.getLogger(__name__)


class AccessScreenBridge:
    def __init__(self, feedback, handler=None, service=None, devices=None, config=None, clock=None):
        self.feedback = feedback
        self.handler = handler
        self.service = service
        self.config = config or feedback.config.face
        self.clock = clock or time.monotonic
        self.lock_control = AccessLockControl(devices or feedback.devices, self.config, self.clock)
        self._enroll_until = None
        self._fault = None
        self.enabled = False
        self.session_id = 0
        self._results = queue.SimpleQueue()
        self._lock = threading.Lock()

    def handle_event(self, name):
        if name not in ("access.start", "access.stop", "access.face_enroll"):
            return None
        if self.service is not None:
            return self._service_event(name)
        if name == "access.start":
            with self._lock:
                self.session_id += 1
                self.enabled = True
                session = self.session_id
            self.feedback.activate("access")
        elif name == "access.stop":
            self.reset()
            session = self.session_id
        else:
            session = self.session_id
        if self.handler is None:
            return {"event": name, "handled": False, "sent": [], "session_id": session,
                    "note": "人脸识别/录入服务尚未接入"}
        try:
            result = self.handler(name)
        except Exception:
            if name == "access.start":
                self.reset()
            raise
        # 命令返回的 ok/success 只是服务应答；只有明确的 recognized 才是识别结论。
        if name == "access.start" and isinstance(result, dict) and "recognized" in result:
            self.report_result(result["recognized"], session_id=session)
        return {"event": name, "handled": True, "sent": [], "result": result, "session_id": session}

    def _service_event(self, name):
        if name == "access.stop":
            try:
                self.reset()
            finally:
                result = self.service.stop()
        elif name == "access.face_enroll":
            result = self.service.enroll()
            if result.get("accepted"):
                self._fault = None
                self._enroll_until = None
                self.feedback.release("access")
                self.feedback.activate("face_enroll")
        else:
            if self.enabled:
                return {"event": name, "handled": False, "sent": [], "note": "门禁识别已启动"}
            with self._lock:
                self.session_id += 1
                session = self.session_id
            result = self.service.start(session)
            if result.get("accepted"):
                self.enabled = True
                self._fault = None
                self._enroll_until = None
                self.feedback.release("face_enroll")
                self.feedback.activate("access")
        return {"event": name, "handled": bool(result.get("accepted")), "sent": [],
                "result": result, "note": result.get("note", ""), "session_id": self.session_id}

    def report_result(self, recognized, session_id):
        """识别线程调用此接口，GPIO 写入留给工程主循环。"""
        if not isinstance(recognized, bool):
            raise ValueError("门禁识别结果 recognized 必须是布尔值")
        if isinstance(session_id, bool) or not isinstance(session_id, int):
            raise ValueError("识别结果必须携带启动时的 session_id")
        with self._lock:
            if not self.enabled or session_id != self.session_id:
                return False
            self._results.put((session_id, recognized))
        return True

    def tick(self):
        if self.service is not None:
            records = self.service.poll_results()
            state = self.service.snapshot()
            if state.get("error"):
                # 失败优先于同一批里尚未应用的识别成功，撤销开锁条件。
                self.lock_control.close()
                with self._lock:
                    self.enabled = False
                    self.session_id += 1
                while not self._results.empty():
                    self._results.get_nowait()
                fault = (state.get("mode"), state["error"])
                if self._fault != fault:
                    self._fault = fault
                    LOGGER.error("人脸任务失败：%s", state["error"])
                    if state.get("mode") == "enroll":
                        self.feedback.set_result("face_enroll", False)
                        self._enroll_until = self.clock() + self.config.enroll_indicator_seconds
                    elif state.get("mode") == "recognize":
                        self.feedback.activate("access")
                records = []
            latest = None
            for result in records:
                if result["event"] == "enrollment":
                    self.feedback.set_result("face_enroll", result["success"])
                    self._enroll_until = self.clock() + self.config.enroll_indicator_seconds
                    person = result.get("person") or {}
                    LOGGER.info("%s：编号=%s，姓名=%s", result["note"], person.get("person_id"), person.get("name"))
                elif self.enabled and result.get("session_id") == self.session_id:
                    latest = result
            if latest is not None:
                # 摄像头比主循环快时，只采用最后的状态，避免旧成功短暂开锁。
                while not self._results.empty():
                    self._results.get_nowait()
                recognized = latest["recognized"]
                if recognized is None:
                    self.feedback.activate("access")
                    self.lock_control.close()
                else:
                    self.feedback.recognition(recognized)
                    self.lock_control.result(recognized)
                LOGGER.info("门禁：%s；姓名=%s；分数=%s", latest["note"], latest.get("name"), latest.get("score"))
            if state.get("phase") == "completed" and state.get("mode") == "recognize":
                # 空人脸库任务已经结束，允许随后的录入。
                self._drain_results()
                self.enabled = False
        self._drain_results()
        if self._enroll_until is not None and self.clock() >= self._enroll_until:
            self.feedback.release("face_enroll")
            self._enroll_until = None
        self.lock_control.tick()

    def _drain_results(self):
        while True:
            try:
                session, recognized = self._results.get_nowait()
            except queue.Empty:
                break
            if self.enabled and session == self.session_id:
                self.feedback.recognition(recognized)
                self.lock_control.result(recognized)

    def reset(self):
        with self._lock:
            self.enabled = False
            self.session_id += 1
        self.feedback.release("access")
        self.feedback.release("face_enroll")
        self._enroll_until = None
        self._fault = None
        self.lock_control.close()

    def close(self):
        if self.service is not None or self.enabled:
            return self.handle_event("access.stop")
        self.reset()
