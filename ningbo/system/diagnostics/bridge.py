"""状态检测按钮与后台检测结果的串口屏适配。"""
import screen_registry as addresses
import logging
import time

LOGGER = logging.getLogger(__name__)


class DiagnosticsScreenBridge:
    def __init__(self, screen, devices, clock=None):
        self.screen = screen
        self.devices = devices
        self._last_revision = 0
        self.clock = clock or time.monotonic
        self._checking_until = 0
        self._holding_checking = False
        self._refresh_remaining = 0
        self._next_refresh = 0

    def handle_event(self, name):
        selection = name[len("diagnostics."):]
        if selection in ("all", "sensors", "actuators"):
            result = self.devices.request_diagnostics(group=selection)
        else:
            result = self.devices.request_diagnostics(target=selection)
        if result["accepted"]:
            # Fast/cached checks may finish before the first display tick.
            # Hold the visual start state so the IPC word cache cannot merge
            # it straight into the final result. Sampling is never delayed.
            self._checking_until = None
            self._holding_checking = True
            self._last_revision = -1
            self._refresh_remaining = 0
        return {"event": name, "handled": result["accepted"], "sent": [],
                "note": result["note"], "result": result}

    def tick(self):
        read_report = getattr(self.devices, "diagnostic_snapshot", None)
        if read_report is None:
            return []
        report = read_report()
        now = self.clock()
        if self._holding_checking and self._checking_until is None:
            self._checking_until = now + .5
        checking_expired = self._holding_checking and now >= self._checking_until
        refresh_due = self._refresh_remaining > 0 and now >= self._next_refresh
        if report["revision"] == self._last_revision and not checking_expired and not refresh_due:
            return []
        if checking_expired:
            self._holding_checking = False
        statuses = {name: ("checking" if self._holding_checking else result["status"])
                    for name, result in report["results"].items()
                    if name in addresses.STATUS_RESULT_ADDRESS_MAP}
        sent = self.screen.handle_message({
            "scene": "diagnostics",
            "statuses": statuses,
            "busy": report["busy"] or self._holding_checking,
            "progress": 0 if self._holding_checking else report["progress"],
            "overall": "checking" if self._holding_checking else report["overall"],
        })
        for item in sent:
            name = item.get("name", "").split(".", 1)[-1]
            result = report["results"].get(name)
            if result is not None:
                LOGGER.info("状态检测提交写屏 %s: %s；VP=0x%04X；%s；帧=%s",
                            name, statuses[name], item["address"],
                            "" if self._holding_checking else result.get("note", ""), item.get("hex", ""))
        self._last_revision = report["revision"]
        if not report["busy"] and not self._holding_checking:
            self._refresh_remaining = self._refresh_remaining - 1 if refresh_due else 2
            self._next_refresh = now + .75
        return sent
