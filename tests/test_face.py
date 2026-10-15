"""人脸库事务、匹配拒绝条件、视觉线程以及串口屏真实按钮的验证。"""
from dataclasses import replace
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Sensor.sensors.usb_camera import UsbCamera
from JETSON.Vision.features import normalized_feature
from JETSON.Vision.library import FaceLibrary, match_templates
from JETSON.Vision.service import FaceService
from Serial_Screen.device_bridge import ScreenDeviceBridge
from Serial_Screen.screen_service.screen.screen_events import ScreenEventParser
from business.access_control import AccessLockControl
from test_device_bridge import FakeDevice, MemoryScreen


def vector(index=0):
    value = np.zeros(128, dtype=np.float32)
    value[index] = 1
    return value


def observation(feature=None, count=1):
    return {"valid": feature is not None, "feature": feature, "face_count": count, "note": "fixture"}


def wait_for(condition, timeout=2):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("视觉任务没有在预期时间到达测试阶段")
        time.sleep(0.002)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = FaceLibrary(Path(self.temp.name) / "faces.sqlite3")
        self.library.initialize("test-model")

    def test_persistence_automatic_id_and_rename(self):
        person = self.library.add_person([vector(), vector()])
        self.assertEqual(person["name"], "人员0001")
        reopened = FaceLibrary(self.library.path)
        self.assertEqual(reopened.list_people()[0]["samples"], 2)
        reopened.rename(person["person_id"], "张三")
        self.assertEqual(reopened.templates("test-model")[0]["name"], "张三")
        self.assertEqual(reopened.add_person([vector(1)], "李四")["person_id"], 2)

    def test_invalid_samples_never_create_partial_person(self):
        for bad in (np.zeros(128), np.ones(127), np.full(128, np.nan), np.full(128, np.inf)):
            with self.subTest(value=bad[0]), self.assertRaises(ValueError):
                self.library.add_person([vector(), bad])
        self.assertEqual(self.library.list_people(), [])

    def test_model_mismatch_rejected_and_original_intact(self):
        self.library.add_person([vector()])
        with self.assertRaises(ValueError):
            self.library.initialize("different-model")
        with self.assertRaises(ValueError):
            self.library.templates("different-model")
        self.assertEqual(len(self.library.templates("test-model")), 1)

    def test_unrelated_sqlite_not_overwritten(self):
        path = Path(self.temp.name) / "other.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE important(value TEXT)")
            db.execute("INSERT INTO important VALUES ('preserve')")
            db.commit()
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            FaceLibrary(path).initialize("model")
        self.assertEqual(path.read_bytes(), original)

    def test_corrupt_template_rejected(self):
        self.library.add_person([vector()])
        with closing(sqlite3.connect(self.library.path)) as db:
            db.execute("UPDATE templates SET vector=?", (b"broken",))
            db.commit()
        with self.assertRaisesRegex(ValueError, "损坏"):
            self.library.templates("test-model")

    def test_matching_groups_person_templates_and_rejects_ambiguity(self):
        templates = [{"person_id": 1, "name": "one", "feature": vector()}] * 3
        self.assertTrue(match_templates(vector(), templates, .363, .05)["recognized"])
        self.assertFalse(match_templates(vector(1), templates, .363, .05)["recognized"])
        templates.append({"person_id": 2, "name": "two", "feature": vector()})
        self.assertFalse(match_templates(vector(), templates, .363, .05)["recognized"])
        self.assertFalse(match_templates(vector(), [], .363, .05)["recognized"])

    def test_threshold_applied_to_normalized_cosine(self):
        templates = [{"person_id": 1, "name": "one", "feature": vector()}]
        query = vector() * .36 + vector(1) * np.sqrt(1 - .36**2)
        self.assertFalse(match_templates(query, templates, .363, .05)["recognized"])
        query = vector() * .37 + vector(1) * np.sqrt(1 - .37**2)
        self.assertTrue(match_templates(query, templates, .363, .05)["recognized"])


class CameraFixture:
    def __init__(self, config):
        self.opened = False
        self.closed = False
        self.thread_ids = []
        self.fail = False
        self.block = None

    def start(self):
        self.opened = True
        self.thread_ids.append(threading.get_ident())

    def read(self):
        self.thread_ids.append(threading.get_ident())
        if self.block is not None:
            self.block.wait(2)
        if self.fail:
            raise OSError("camera disconnected")
        return object()

    def close(self):
        self.closed = True
        self.thread_ids.append(threading.get_ident())


class EngineFixture:
    signature = "test-model"

    def __init__(self, observations):
        self.observations = list(observations)
        self.count = 0
        self.load_gate = None

    def load(self):
        if self.load_gate is not None:
            self.load_gate.wait(2)

    def analyze(self, frame):
        self.count += 1
        return self.observations.pop(0) if self.observations else observation(count=0)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(DEFAULT_CONFIG.face, database_path=str(Path(self.temp.name) / "faces.sqlite3"),
                              enroll_samples=3, enroll_interval=.005, processing_interval=.001, enroll_timeout=.1)
        self.camera = CameraFixture(DEFAULT_CONFIG.camera)

    def service(self, observations):
        self.engine = EngineFixture(observations)
        service = FaceService(self.config, DEFAULT_CONFIG.camera, camera_factory=lambda cfg: self.camera,
                              engine_factory=lambda cfg: self.engine)
        self.addCleanup(service.stop)
        return service

    def complete(self, service):
        wait_for(lambda: service.snapshot()["phase"] in ("completed", "error"))
        return service.snapshot()

    def test_enroll_multiframe_and_duplicate_without_new_person(self):
        service = self.service([observation(vector())] * 40)
        self.assertFalse(self.camera.opened)
        self.assertTrue(service.enroll()["accepted"])
        self.assertEqual(self.complete(service)["phase"], "completed")
        self.assertEqual(service.library.list_people()[0]["samples"], 3)
        self.assertEqual(len(set(self.camera.thread_ids)), 1)
        self.assertTrue(self.camera.closed)
        self.engine.observations = [observation(vector())] * 40
        wait_for(lambda: not service.snapshot()["worker_alive"])
        self.assertTrue(service.enroll()["accepted"])
        self.assertTrue(self.complete(service)["person"]["duplicate"])
        self.assertEqual(len(service.library.list_people()), 1)

    def test_no_face_or_multiple_faces_cannot_be_enrolled(self):
        service = self.service([observation(count=2), observation(count=0)] * 100)
        service.enroll()
        self.assertEqual(self.complete(service)["phase"], "error")
        self.assertEqual(service.library.list_people(), [])

    def test_startup_name_used_once_then_automatic_name(self):
        self.config = replace(self.config, enroll_name="张三")
        service = self.service([observation(vector())] * 40)
        service.enroll()
        self.assertEqual(self.complete(service)["person"]["name"], "张三")
        wait_for(lambda: not service.snapshot()["worker_alive"])
        self.engine.observations = [observation(vector(1))] * 40
        service.enroll()
        self.assertEqual(self.complete(service)["person"]["name"], "人员0002")

    def test_changed_person_rejects_enrollment(self):
        service = self.service([observation(vector())] + [observation(vector(1))] * 50)
        service.enroll()
        self.assertIn("不一致", self.complete(service)["error"])
        self.assertEqual(service.library.list_people(), [])

    def test_empty_library_does_not_open_camera(self):
        service = self.service([])
        service.start(7)
        self.complete(service)
        result = service.poll_results()[0]
        self.assertFalse(result["recognized"])
        self.assertEqual(result["session_id"], 7)
        self.assertFalse(self.camera.opened)

    def test_stable_match_unknown_and_no_face(self):
        service = self.service([observation(vector())] * 3 + [observation(vector(1)), observation(count=0)])
        service.library.initialize(self.engine.signature)
        service.library.add_person([vector()])
        service.start(5)
        wait_for(lambda: self.engine.count >= 5)
        results = service.poll_results()
        self.assertEqual([row["recognized"] for row in results], [None, True, False, None])
        self.assertTrue(all(row["session_id"] == 5 for row in results))
        service.stop()
        self.assertEqual(service.poll_results(), [])

    def test_camera_failure_closes_on_worker_and_reports_error(self):
        service = self.service([])
        self.camera.fail = True
        service.enroll()
        self.assertIn("disconnected", self.complete(service)["error"])
        self.assertTrue(self.camera.closed)
        self.assertEqual(len(set(self.camera.thread_ids)), 1)
        self.assertEqual(service.library.list_people(), [])

    def test_stop_during_model_load_never_opens_camera(self):
        service = self.service([])
        self.engine.load_gate = threading.Event()
        service.join_timeout = .01
        service.enroll()
        with self.assertRaises(RuntimeError):
            service.stop()
        self.assertFalse(service.enroll()["accepted"])
        self.engine.load_gate.set()
        wait_for(lambda: not service.snapshot()["worker_alive"])
        service.stop()
        self.assertFalse(self.camera.opened)

    def test_blocked_read_not_released_from_other_thread(self):
        service = self.service([])
        service.join_timeout = .01
        self.camera.block = threading.Event()
        service.enroll()
        wait_for(lambda: len(self.camera.thread_ids) >= 2)
        try:
            with self.assertRaises(RuntimeError):
                service.stop()
            self.assertFalse(self.camera.closed)
            self.assertFalse(service.enroll()["accepted"])
        finally:
            self.camera.block.set()
        wait_for(lambda: self.camera.closed)
        service.stop()
        self.assertEqual(service.library.list_people(), [])


class ServiceStub:
    def __init__(self):
        self.calls = []
        self.records = []
        self.state = {"phase": "idle", "mode": None, "error": ""}

    def enroll(self):
        self.calls.append("enroll")
        self.state.update(phase="enrolling", mode="enroll", error="")
        return {"accepted": True}

    def start(self, token):
        self.calls.append(("start", token))
        self.state.update(phase="recognizing", mode="recognize", error="")
        return {"accepted": True}

    def stop(self):
        self.calls.append("stop")
        self.records.clear()
        self.state.update(phase="idle", mode=None, error="")
        return {"accepted": True}

    def poll_results(self):
        rows, self.records = self.records, []
        return rows

    def snapshot(self):
        return dict(self.state)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.clock = [100.]
        self.service = ServiceStub()
        self.devices = FakeDevice({})
        self.bridge = ScreenDeviceBridge(MemoryScreen(), self.devices, access_service=self.service,
                                         clock=lambda: self.clock[0])

    def color(self):
        return self.bridge.feedback.color()

    def test_screen_enrollment_address_and_completion_no_new_vp(self):
        event = ScreenEventParser().feed(bytes.fromhex("5A A5 06 83 30 10 01 50 10"))[0]
        self.assertTrue(self.bridge.handle_event(event)["handled"])
        self.assertEqual(self.service.calls, ["enroll"])
        self.assertEqual(self.color(), "blue")
        self.service.records = [{"event": "enrollment", "success": True,
                                 "person": {"person_id": 1, "name": "人员0001"}, "note": "录入完成"}]
        self.bridge.tick()
        self.assertEqual(self.color(), "green")
        self.clock[0] += 3
        self.bridge.tick()
        self.assertEqual(self.color(), "off")
        self.assertEqual(self.bridge.screen.frames, [])

    def result(self, value, token):
        return {"event": "recognition", "recognized": value, "session_id": token, "note": "test"}

    def test_match_unknown_waiting_and_stale_session(self):
        event = ScreenEventParser().feed(bytes.fromhex("5A A5 06 83 30 20 01 50 20"))[0]
        token = self.bridge.handle_event(event)["session_id"]
        for value, color in ((True, "green"), (False, "red"), (None, "blue")):
            self.service.records = [self.result(value, token)]
            self.bridge.tick()
            self.assertEqual(self.color(), color)
        self.bridge.handle_event("access.stop")
        self.bridge.handle_event("access.start")
        self.service.records = [self.result(True, token)]
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")

    def test_error_discards_queued_success(self):
        token = self.bridge.handle_event("access.start")["session_id"]
        self.service.records = [self.result(True, token)]
        self.service.state.update(phase="error", error="USB removed")
        with self.assertLogs("Serial_Screen.access_bridge", level="ERROR"):
            self.bridge.tick()
        self.assertEqual(self.color(), "blue")
        self.assertFalse(self.bridge.access.enabled)
        self.assertFalse(any(name == "lock" for name, _ in self.devices.calls))

    def test_latest_pending_supersedes_old_success_without_lock_pulse(self):
        self.bridge.access.config = replace(DEFAULT_CONFIG.face, unlock_on_match=True)
        self.bridge.access.lock_control.config = self.bridge.access.config
        token = self.bridge.handle_event("access.start")["session_id"]
        self.service.records = [self.result(True, token), self.result(None, token)]
        self.bridge.tick()
        self.assertEqual(self.color(), "blue")
        self.assertFalse(any(name == "lock" for name, _ in self.devices.calls))

    def test_program_stop_cancels_enrollment_even_when_access_disabled(self):
        self.bridge.handle_event("access.face_enroll")
        self.assertFalse(self.bridge.access.enabled)
        self.bridge.stop()
        self.assertEqual(self.service.calls, ["enroll", "stop"])

    def test_completed_empty_gallery_can_then_enroll(self):
        token = self.bridge.handle_event("access.start")["session_id"]
        self.service.records = [self.result(False, token)]
        self.service.state.update(phase="completed")
        self.bridge.tick()
        self.assertEqual(self.color(), "red")
        self.assertFalse(self.bridge.access.enabled)
        self.bridge.handle_event("access.face_enroll")
        self.assertEqual(self.color(), "blue")


class LockTests(unittest.TestCase):
    def test_default_no_unlock_and_optional_pulse_retries_failed_close(self):
        devices = FakeDevice({})
        control = AccessLockControl(devices, DEFAULT_CONFIG.face)
        control.result(True)
        self.assertEqual(devices.calls, [])
        clock = [100.]
        control = AccessLockControl(devices, replace(DEFAULT_CONFIG.face, unlock_on_match=True), lambda: clock[0])
        control.result(True)
        self.assertEqual(devices.calls[-1], ("lock", {"on": True}))
        with patch.object(devices, "control", side_effect=OSError("failed")), self.assertRaises(OSError):
            control.close()
        control.tick()  # 关闭失败后不等待原定时长，立即重试。
        self.assertEqual(devices.calls[-1], ("lock", {"on": False}))
        control.result(True)
        clock[0] += 4
        control.tick()
        self.assertEqual(devices.calls[-1], ("lock", {"on": False}))


class ConfigTests(unittest.TestCase):
    def test_invalid_camera_and_face_options(self):
        for device in (-1, True, "", "https://remote"):
            with self.subTest(device=device), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, camera=replace(DEFAULT_CONFIG.camera, device=device)).validate()
        for values in ({"feature_dimension": 64}, {"enroll_samples": 0}, {"enroll_timeout": .1},
                       {"similarity_threshold": float("nan")}, {"unlock_on_match": "yes"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, face=replace(DEFAULT_CONFIG.face, **values)).validate()


class CameraDriverTests(unittest.TestCase):
    def test_source_resolution_request_and_read_disconnect(self):
        from unittest.mock import MagicMock
        module = MagicMock()
        capture = module.VideoCapture.return_value
        capture.isOpened.return_value = True
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        capture.read.side_effect = [(True, frame), (False, None)]
        camera = UsbCamera(replace(DEFAULT_CONFIG.camera, device=0), module=module)
        self.assertFalse(module.VideoCapture.called)
        camera.start()
        self.assertEqual(module.VideoCapture.call_args.args[0], 0)
        self.assertIs(camera.read(), frame)
        with self.assertRaises(OSError):
            camera.read()
        camera.close()
        capture.release.assert_called_once()

    def test_missing_path_and_open_failure(self):
        from unittest.mock import MagicMock
        module = MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            camera = UsbCamera(replace(DEFAULT_CONFIG.camera, device=str(Path(folder) / "missing")), module)
            with self.assertRaises(FileNotFoundError):
                camera.start()
            self.assertFalse(module.VideoCapture.called)
        module.VideoCapture.return_value.isOpened.return_value = False
        with self.assertRaises(OSError):
            UsbCamera(replace(DEFAULT_CONFIG.camera, device=0), module).start()
        module.VideoCapture.return_value.release.assert_called_once()


class CommandTests(unittest.TestCase):
    def test_rename_from_terminal_without_hardware_or_screen_ui(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from main import main
        with tempfile.TemporaryDirectory() as folder:
            library = FaceLibrary(Path(folder) / "faces.sqlite3")
            library.initialize("test-model")
            library.add_person([vector()])
            with redirect_stdout(StringIO()), patch("main.NingBoApplication") as application:
                self.assertEqual(main(["--face-db", str(library.path), "--rename-face", "1", "张三"]), 0)
                self.assertEqual(main(["--face-db", str(library.path), "--list-faces"]), 0)
            application.assert_not_called()
            self.assertEqual(library.list_people()[0]["name"], "张三")


if __name__ == "__main__":
    unittest.main()
