"""状态检测任务调度，具体判断交给输入和输出检测器。"""
from copy import deepcopy
import threading
import logging
import time


from system.diagnostics.jetson.definitions import SENSOR_TARGETS, ACTUATOR_TARGETS, TARGETS, ALIASES, overall_status
from system.diagnostics.jetson.sensor_checks import SensorStatusChecker
from system.diagnostics.jetson.actuator_checks import ActuatorStatusChecker

LOGGER = logging.getLogger(__name__)


class DiagnosticService:
    def __init__(self, manager):
        self.manager = manager
        self.sensor_checker = SensorStatusChecker(manager)
        self.actuator_checker = ActuatorStatusChecker(manager)
        self._check_locks = {name: threading.RLock() for name in ACTUATOR_TARGETS}
        self._job_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancel = threading.Event()
        self._worker = None
        self._sensor_jobs = {}
        self._actuator_jobs = {}
        self._sensor_jobs_lock = threading.Lock()
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
        # 传感器不得共用执行器检测锁，否则烟雾无响应会阻塞全部传感器。
        if name in SENSOR_TARGETS:
            return self._checked_sensor(name)
        with self._check_locks[name]:
            try:
                state = self.actuator_checker.check(name, cancel_event=cancel_event)
                if not isinstance(state, dict) or "status" not in state:
                    raise ValueError("执行器未返回有效状态结果")
            except Exception as exc:
                state = {"status": "error", "note": str(exc)}
            return {**state, "name": name, "category": "actuator", "checked_at": time.time()}

    def _checked_sensor(self, name):
        try:
            state = self.sensor_checker.check(name)
            if not isinstance(state, dict) or "status" not in state:
                raise ValueError("传感器未返回有效状态结果")
        except Exception as exc:
            state = {"status": "error", "note": str(exc)}
        return {**state, "name": name, "category": "sensor", "checked_at": time.time()}

    def _sensor_timeout(self, name):
        # 保留已有设备的正常重试窗口，每个期限从该项启动时独立计时。
        if name == "smoke":
            # 单次 UART 读取结束即判定；锁/驱动卡住也不能长期停在检测中。
            return max(1.0, self.manager.config.smoke.timeout + 0.5)
        if name == "dht11":
            cfg = self.manager.config.dht11
            return cfg.attempts * (max(cfg.interval, cfg.retry_interval) + cfg.native_timeout_seconds) + 3.0
        return 3.0

    def _check_timeout(self, name):
        if name in SENSOR_TARGETS:
            return self._sensor_timeout(name)
        if name in ("servo", "fan", "stepper"):
            # 最多三轮，运行反馈与停止基线均需真实采样；两轮通过即可结束。
            return 20.0
        # 各项独立计时；保留真实动作/采样时间，超时不会让其它结果停在检测中。
        return 15.0

    def _start_sensor(self, name):
        with self._sensor_jobs_lock:
            jobs = self._sensor_jobs if name in SENSOR_TARGETS else self._actuator_jobs
            previous = jobs.get(name)
            if previous is not None and previous["thread"].is_alive():
                # Python 无法强杀阻塞的驱动调用；每项最多保留一个读取线程。
                return None
            job = {"done": threading.Event(), "result": None,
                   "started": time.monotonic(), "timeout": self._check_timeout(name),
                   "cancel": threading.Event()}
            def collect():
                try:
                    job["result"] = self.check(name, cancel_event=job["cancel"])
                except Exception as exc:
                    job["result"] = self._sensor_failure(name, str(exc))
                finally:
                    job["finished"] = time.monotonic()
                    job["done"].set()
            worker = threading.Thread(target=collect, name="diagnostic-" + name, daemon=True)
            job["thread"] = worker
            jobs[name] = job
            worker.start()
            return job

    def _sensor_failure(self, name, note):
        return {"name": name, "category": "sensor" if name in SENSOR_TARGETS else "actuator", "status": "error",
                "note": note, "checked_at": time.time()}

    def _run_sensors(self, targets, publish):
        pending = {}
        def launch(name):
            job = self._start_sensor(name)
            if job is None:
                publish(name, self._sensor_failure(name, "上轮读取尚未退出，本轮无法取得新的响应"))
            else:
                LOGGER.info("状态检测开始 %s，独立等待上限 %.1f 秒", name, job["timeout"])
                pending[name] = job
        # 舵机需要捕捉短运动窗口，先完成再启动其他ADC密集检测。
        # 各项超时仍从真正启动时计时，普通传感器不会等待舵机。
        deferred = [name for name in targets if 'servo' in targets
                    and ((name in ACTUATOR_TARGETS and name != 'servo') or name == 'current')]
        initial = (['servo'] if 'servo' in targets else []) + [
            name for name in targets if name != 'servo' and name not in deferred]
        for name in initial:
            if self._cancel.is_set():
                return
            launch(name)
        if 'servo' not in pending:
            for name in deferred:
                if self._cancel.is_set():
                    return
                launch(name)
            deferred = []
        while pending and not self._cancel.is_set():
            now = time.monotonic()
            for name, job in list(pending.items()):
                done = job["done"].is_set()
                if done and job["finished"] - job["started"] <= job["timeout"]:
                    result = job["result"] or self._sensor_failure(name, "检测线程结束但没有返回结果")
                elif now - job["started"] >= job["timeout"]:
                    job["cancel"].set()
                    result = self._sensor_failure(name,
                        f"{job['timeout']:g} 秒内未取得有效检测结果，读取或资源等待超时")
                else:
                    continue
                publish(name, result)
                del pending[name]
            if deferred and 'servo' not in pending:
                for name in deferred:
                    if self._cancel.is_set():
                        break
                    launch(name)
                deferred = []
            if pending:
                self._cancel.wait(.02)
        if self._cancel.is_set():
            for job in pending.values():
                job["cancel"].set()

    def check_all(self, group="all"):
        if not self.manager._started:
            raise RuntimeError("请先启动设备服务")
        targets = self.targets(group)
        results = {}
        self._run_sensors(targets,
                          lambda name, result: results.update({name: result}))
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
        def publish(name, result):
            results[name] = result
            with self._state_lock:
                self._report["results"][name] = result
                self._report["progress"] = round(len(results) * 100 / len(targets))
                self._report["revision"] += 1
            LOGGER.info("状态检测完成 %s: %s；%s", name, result["status"], result.get("note", ""))
        try:
            self._run_sensors(targets, publish)
        except Exception:
            LOGGER.exception("状态检测任务异常退出")
        finally:
            with self._state_lock:
                cancelled = self._cancel.is_set()
                for name in targets:
                    if name not in results:
                        self._report["results"][name] = {"name": name,
                            "status": "unknown" if cancelled else "error",
                            "note": "检测已取消" if cancelled else "状态检测任务未能完成"}
                self._report.update(busy=False, phase="cancelled" if cancelled else "completed")
                if not cancelled:
                    self._report["progress"] = 100
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
            with self._sensor_jobs_lock:
                jobs = list(self._sensor_jobs.values()) + list(self._actuator_jobs.values())
                for job in jobs:
                    job["cancel"].set()
                workers = [job["thread"] for job in jobs]
            deadline = time.monotonic() + self.manager.config.thread_join_timeout
            for worker in workers:
                worker.join(max(0, deadline - time.monotonic()))
            if any(worker.is_alive() for worker in workers):
                raise RuntimeError("状态检测读取线程尚未退出，硬件资源保持打开")
