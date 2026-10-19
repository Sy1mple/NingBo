"""状态检测任务调度，具体判断交给输入和输出检测器。"""
from copy import deepcopy
import threading
import time


from system.diagnostics.jetson.definitions import SENSOR_TARGETS, ACTUATOR_TARGETS, TARGETS, ALIASES, overall_status
from system.diagnostics.jetson.sensor_checks import SensorStatusChecker
from system.diagnostics.jetson.actuator_checks import ActuatorStatusChecker

class DiagnosticService:
    def __init__(self, manager):
        self.manager = manager
        self.sensor_checker = SensorStatusChecker(manager)
        self.actuator_checker = ActuatorStatusChecker(manager)
        self._check_lock = threading.RLock()
        self._job_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancel = threading.Event()
        self._worker = None
        self._report = {"revision": 0, "busy": False, "phase": "idle", "results": {},
                        "progress": 0, "overall": "unknown"}

    @staticmethod
    def targets(group="all", target=None):
        if target is not None:
            target = ALIASES.get(target, target)
            if target not in TARGETS:
                raise ValueError(f"未知状态检测设备: {target}")
            return (target,)
        if group == "all": return TARGETS
        if group == "sensors": return SENSOR_TARGETS
        if group == "actuators": return ACTUATOR_TARGETS
        raise ValueError("状态检测分组必须是 all、sensors 或 actuators")

    def check(self, name, cancel_event=None):
        name = self.targets(target=name)[0]
        if not self.manager._started:
            raise RuntimeError("请先启动设备服务")
        with self._check_lock:
            try:
                if name in SENSOR_TARGETS:
                    state = self.sensor_checker.check(name)
                else:
                    state = self.actuator_checker.check(name, cancel_event=cancel_event)
            except Exception as exc:
                state = {"status": "error", "note": str(exc)}
            return {**state, "name": name,
                    "category": "sensor" if name in SENSOR_TARGETS else "actuator",
                    "checked_at": time.time()}

    def check_all(self, group="all"):
        results = {name: self.check(name) for name in self.targets(group)}
        return {"results": results, "overall": overall_status(results), "busy": False, "progress": 100}

    def request(self, group="all", target=None):
        targets = self.targets(group, target)
        if not self.manager._started:
            raise RuntimeError("请先启动设备服务")
        with self._job_lock:
            if self._worker is not None and self._worker.is_alive():
                return {"accepted": False, "busy": True, "note": "状态检测正在进行"}
            self._cancel.clear()
            with self._state_lock:
                self._report = {"revision": self._report["revision"] + 1, "busy": True,
                                "phase": "checking", "results": {name: {"name": name, "status": "checking"} for name in targets},
                                "progress": 0, "overall": "checking"}
            self._worker = threading.Thread(target=self._run, args=(targets,), name="device-diagnostics", daemon=True)
            self._worker.start()
            return {"accepted": True, "busy": True, "note": "已开始状态检测"}

    def _run(self, targets):
        results = {}
        try:
            for name in targets:
                if self._cancel.is_set():
                    break
                result = self.check(name, cancel_event=self._cancel)
                results[name] = result
                with self._state_lock:
                    self._report["results"][name] = result
                    self._report["progress"] = round(len(results) * 100 / len(targets))
                    self._report["revision"] += 1
        finally:
            with self._state_lock:
                self._report.update(busy=False, phase="cancelled" if self._cancel.is_set() else "completed")
                if self._cancel.is_set():
                    for name in targets:
                        if name not in results:
                            self._report["results"][name] = {"name": name, "status": "unknown", "note": "检测已取消"}
                self._report["overall"] = overall_status(self._report["results"])
                self._report["revision"] += 1

    def snapshot(self):
        with self._state_lock:
            return deepcopy(self._report)

    def stop(self):
        with self._job_lock:
            self._cancel.set()
            if self._worker is not None:
                self._worker.join(self.manager.config.thread_join_timeout)
                if self._worker.is_alive():
                    raise RuntimeError("状态检测线程尚未结束，设备资源保持打开")
                self._worker = None
