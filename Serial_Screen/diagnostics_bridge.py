"""状态检测按钮与后台检测结果的串口屏适配。"""
from Serial_Screen.screen_service.screen import addresses


class DiagnosticsScreenBridge:
    def __init__(self, screen, devices):
        self.screen = screen
        self.devices = devices
        self._last_revision = 0

    def handle_event(self, name):
        selection = name[len("diagnostics."):]
        if selection in ("all", "sensors", "actuators"):
            result = self.devices.request_diagnostics(group=selection)
        else:
            result = self.devices.request_diagnostics(target=selection)
        return {"event": name, "handled": result["accepted"], "sent": [],
                "note": result["note"], "result": result}

    def tick(self):
        read_report = getattr(self.devices, "diagnostic_snapshot", None)
        if read_report is None:
            return []
        report = read_report()
        if report["revision"] == self._last_revision:
            return []
        sent = self.screen.handle_message({
            "scene": "diagnostics",
            "statuses": {name: result["status"] for name, result in report["results"].items()
                         if name in addresses.STATUS_RESULT_ADDRESS_MAP},
            "busy": report["busy"], "progress": report["progress"], "overall": report["overall"],
        })
        self._last_revision = report["revision"]
        return sent
