"""摄像头、人脸模型和人脸库编排；识别与录入共用一个后台任务。"""
from collections import deque
from copy import deepcopy
import threading
import time

from experiments.access.jetson.camera import UsbCamera
from experiments.access.jetson.engine import FaceEngine
from experiments.access.jetson.features import normalized_feature
from experiments.access.jetson.library import FaceLibrary, match_templates


class FaceService:
    def __init__(self, config, camera_config, join_timeout=5.0, camera_factory=None, engine_factory=None, library=None,
                 cloud_client_factory=None, preview=None):
        self.config = config
        self.camera_config = camera_config
        self.join_timeout = join_timeout
        self.camera_factory = camera_factory or UsbCamera
        self.preview = preview
        self._camera_stream = None
        self.engine_factory = engine_factory or FaceEngine
        if config.backend == 'xfyun':
            from experiments.access.jetson.cloud_library import CloudFaceLibrary
            from experiments.access.jetson.xfyun_client import XfyunFaceClient
            self.library = library or CloudFaceLibrary(config.database_path)
            self.cloud_client_factory = cloud_client_factory or XfyunFaceClient
        else:
            self.library = library or FaceLibrary(config.database_path)
        self._enroll_name = config.enroll_name
        self._engine = None
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._worker = None
        self._cancel = threading.Event()
        self._generation = 0
        self._results = deque(maxlen=config.result_queue_size)
        self._state = {"phase": "idle", "mode": None, "error": "", "note": "", "samples": 0, "person": None, "recognized": None,
                       "backend": config.backend, "precision": config.precision, "dla_core": config.dla_core}

    def start(self, session_id):
        if isinstance(session_id, bool) or not isinstance(session_id, int):
            raise ValueError("识别请求须携带门禁会话编号")
        return self._request("recognize", session_id=session_id)

    def enroll(self):
        return self._request("enroll")

    def _request(self, mode, session_id=None):
        with self._lifecycle_lock:
            if not self.config.enabled:
                return {"accepted": False, "note": "人脸功能在 config.py 中未启用"}
            if self._worker is not None and self._worker.is_alive():
                return {"accepted": False, "note": "摄像头任务正在进行，请先停止当前识别/录入"}
            if self._camera_stream is not None and self._camera_stream.snapshot()['worker_alive']:
                return {"accepted": False, "note": "上一轮摄像头采集尚未释放，请等待读取返回后重试"}
            with self._lock:
                self._generation += 1
                generation = self._generation
                self._results.clear()
                self._cancel = threading.Event()
                self._state.update(phase="starting", mode=mode, error="", note="正在初始化人脸服务和摄像头", samples=0, person=None, recognized=None)
            self._worker = threading.Thread(target=self._run, args=(mode, session_id, generation, self._cancel),
                                            name="usb-face-" + mode, daemon=True)
            try:
                self._worker.start()
            except Exception:
                self._worker = None
                with self._lock:
                    self._state.update(phase="error", error="视觉线程启动失败")
                raise
            return {"accepted": True, "note": "已启动人脸录入" if mode == "enroll" else "已启动门禁识别"}

    def camera_for_task(self):
        if self.preview is None:
            return self.camera_factory(self.camera_config)
        if self._camera_stream is None:
            from experiments.access.jetson.camera_stream import CameraStream
            self._camera_stream = CameraStream(self.camera_config, self.preview.hub,
                                               camera_factory=self.camera_factory, join_timeout=self.join_timeout)
        return self._camera_stream

    def start_camera(self, camera, cancel):
        if camera is self._camera_stream:
            camera.start(cancel_event=cancel)
        else:
            camera.start()
        if cancel.is_set():
            raise InterruptedError('人脸任务已停止')
        if self.preview is not None:
            self.preview.open_local_browser()

    def prepare_enrollment(self, cancel):
        if self.preview is None or not self.config.enroll_preview_seconds:
            return not cancel.is_set()
        with self._lock:
            self._state['note'] = '请正对摄像头，正在准备采集录入照片'
        self.snapshot()
        return not cancel.wait(self.config.enroll_preview_seconds)

    def _emit(self, generation, event, **values):
        with self._lock:
            if generation == self._generation and not self._cancel.is_set():
                self._results.append({"event": event, **values})

    def complete_recognition(self, generation, session_id, result):
        with self._lock:
            if generation != self._generation or self._cancel.is_set():
                return
            person = ({k: result[k] for k in ('person_id', 'name')} if result['recognized'] else None)
            self._state.update(recognized=result['recognized'], person=person, note=result['note'])
            self._emit(generation, 'recognition', session_id=session_id, **result)

    def _run(self, mode, session_id, generation, cancel):
        if self.config.backend == 'xfyun':
            from experiments.access.jetson.cloud_service import run_cloud_task
            return run_cloud_task(self, mode, session_id, generation, cancel)
        camera = None
        error = ""
        try:
            if self._engine is None:
                self._engine = self.engine_factory(self.config)
            self._engine.load()
            if cancel.is_set():
                return
            self.library.initialize(self._engine.signature, self.config.feature_dimension)
            gallery = self.library.templates(self._engine.signature)
            if mode == "recognize" and not gallery:
                self.complete_recognition(generation, session_id, dict(recognized=False,
                           person_id=None, name=None, score=None, note="人脸库为空，请先录入"))
                return
            camera = self.camera_for_task()
            self.start_camera(camera, cancel)
            if cancel.is_set():
                return
            with self._lock:
                if generation == self._generation:
                    self._state["phase"] = "enrolling" if mode == "enroll" else "recognizing"
            if mode == "enroll":
                if not self.prepare_enrollment(cancel):
                    return
                self._enroll_loop(camera, gallery, generation, cancel)
            else:
                self._recognize_loop(camera, gallery, session_id, generation, cancel)
        except InterruptedError:
            if not cancel.is_set():
                error = '摄像头请求意外中断'
        except Exception as exc:
            error = str(exc)
        finally:
            if camera is not None:
                try:
                    camera.close()
                except Exception as exc:
                    error = error or str(exc)
            if self._engine is not None and hasattr(self._engine, "close"):
                try:
                    self._engine.close()
                except Exception as exc:
                    error = error or str(exc)
            with self._lock:
                if generation == self._generation:
                    self._state.update(phase="error" if error else "completed", error=error)

    def _enroll_loop(self, camera, gallery, generation, cancel):
        deadline = time.monotonic() + self.config.enroll_timeout
        samples = []
        last_sample = 0.0
        while not cancel.is_set():
            if time.monotonic() >= deadline:
                raise TimeoutError("人脸录入超时，没有收集到足够的清晰单人样本")
            observation = self._engine.analyze(camera.read())
            if cancel.is_set():
                return
            with self._lock:
                self._state["note"] = observation["note"]
            if observation["valid"] and time.monotonic() - last_sample >= self.config.enroll_interval:
                vector = normalized_feature(observation["feature"])
                if samples and float(vector @ samples[0]) < self.config.enroll_consistency:
                    raise ValueError("录入对象发生变化或样本不一致，请重新录入")
                samples.append(vector.copy())
                last_sample = time.monotonic()
                with self._lock:
                    self._state["samples"] = len(samples)
                if len(samples) >= self.config.enroll_samples:
                    # 写库与停止请求共用锁，停止完成后不会出现新的录入提交。
                    with self._lock:
                        if generation != self._generation or cancel.is_set():
                            return
                        mean = normalized_feature(sum(samples))
                        duplicate = match_templates(mean, gallery, self.config.duplicate_threshold, 0)
                        if duplicate["recognized"]:
                            person = {"person_id": duplicate["person_id"], "name": duplicate["name"], "duplicate": True}
                            note = "该人员已在人脸库中，无需重复录入"
                        else:
                            person = self.library.add_person(samples, self._enroll_name)
                            self._enroll_name = ""
                            note = "人脸录入完成"
                        self._state.update(person=person, note=note)
                        self._emit(generation, "enrollment", success=True, person=person, note=note)
                    return
            if cancel.wait(self.config.processing_interval):
                return

    def _recognize_loop(self, camera, gallery, session_id, generation, cancel):
        last_key = None
        candidate = None
        streak = 0
        deadline = time.monotonic() + self.config.verification_timeout
        while not cancel.is_set():
            if time.monotonic() >= deadline:
                self.complete_recognition(generation, session_id, dict(recognized=False, person_id=None,
                                          name=None, score=None, note='身份验证超时，请重新点击验证'))
                return
            observation = self._engine.analyze(camera.read())
            if cancel.is_set():
                return
            if not observation["valid"]:
                candidate, streak = None, 0
                result = {"recognized": False if observation["face_count"] > 1 else None,
                          "person_id": None, "name": None, "score": None, "note": observation["note"]}
            else:
                result = match_templates(observation["feature"], gallery, self.config.similarity_threshold,
                                         self.config.ambiguity_margin)
                if result["recognized"]:
                    streak = streak + 1 if candidate == result["person_id"] else 1
                    candidate = result["person_id"]
                    if streak < self.config.consecutive_matches:
                        result = {**result, "recognized": None, "note": "正在连续确认人员"}
                else:
                    candidate, streak = None, 0
            key = (result["recognized"], result["person_id"], result["note"])
            with self._lock:
                self._state["note"] = result["note"]
            if isinstance(result['recognized'], bool):
                self.complete_recognition(generation, session_id, result)
                return
            if key != last_key:
                self._emit(generation, "recognition", session_id=session_id, **result)
                last_key = key
            if cancel.wait(self.config.processing_interval):
                return

    def poll_results(self):
        with self._lock:
            records = list(self._results)
            self._results.clear()
            return records

    def snapshot(self):
        with self._lock:
            state = {**deepcopy(self._state), "worker_alive": bool(self._worker and self._worker.is_alive())}
        if self.preview is not None:
            self.preview.hub.update_task(state)
            state['preview_url'] = self.preview.url
        return state

    def stop(self):
        with self._lifecycle_lock:
            with self._lock:
                self._generation += 1
                self._cancel.set()
                self._results.clear()
                self._state.update(phase="stopping")
            # 不在别的线程 release 一个正在 read 的 OpenCV 连接。
            if self._worker is not None:
                self._worker.join(self.join_timeout)
                if self._worker.is_alive():
                    with self._lock:
                        self._state.update(phase="error", error="摄像头线程未结束，暂时禁止再次启动")
                    raise RuntimeError("摄像头线程未结束，保留连接直到读取返回")
                self._worker = None
            if self._camera_stream is not None:
                self._camera_stream.close()
            with self._lock:
                self._state.update(phase="idle", mode=None, error="", note="")
            self.snapshot()
            return {"accepted": True, "note": "人脸任务已停止，摄像头已释放"}
