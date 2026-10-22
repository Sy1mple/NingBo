"""An absent or stuck sensor must never hold other result VPs in checking."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from config import DEFAULT_CONFIG
from system.diagnostics.jetson.service import DiagnosticService
from system.diagnostics.bridge import DiagnosticsScreenBridge
from screen_service import MessageParser
from experiments.body_temperature.jetson.driver import Mlx90614Driver


class ParallelSensorTests(unittest.TestCase):
    def service(self):
        manager = SimpleNamespace(_started=True, config=DEFAULT_CONFIG)
        return DiagnosticService(manager)

    def wait_for(self, predicate, timeout=1):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline: self.fail("timed out waiting for result")
            time.sleep(.005)

    def test_unplugged_smoke_does_not_delay_body_result_or_screen_frame(self):
        service = self.service()
        gate, smoke_started = threading.Event(), threading.Event()
        def check(name):
            if name == "smoke":
                smoke_started.set()
                gate.wait(2)
                return {"status": "error", "note": "no smoke frame"}
            return {"status": "normal", "temperature": 24.}
        service.sensor_checker.check = check
        sent = []
        clock = [100.]
        screen = SimpleNamespace(handle_message=lambda msg: sent.extend(MessageParser().parse(msg)) or [])
        bridge = DiagnosticsScreenBridge(screen, SimpleNamespace(
            request_diagnostics=service.request, diagnostic_snapshot=service.snapshot), clock=lambda:clock[0])
        try:
            self.assertTrue(bridge.handle_event("diagnostics.sensors")["handled"])
            self.assertTrue(smoke_started.wait(1))
            self.wait_for(lambda:service.snapshot()["results"]["body_temperature"]["status"] == "normal")
            report = service.snapshot()
            self.assertTrue(report["busy"])
            self.assertEqual(report["results"]["smoke"]["status"], "checking")
            bridge.tick()
            self.assertEqual(next(i for i in sent if i["address"] == 0x6003)["data"], "检".encode("gb2312"))
            sent.clear(); clock[0] += .6; bridge.tick()
            self.assertEqual(next(i for i in sent if i["address"] == 0x6003)["data"], "好".encode("gb2312"))
        finally:
            gate.set(); service._worker.join(2); service.stop()
        self.assertEqual(service.snapshot()["results"]["smoke"]["status"], "error")

    def test_timeout_completes_report_no_thread_accumulation_no_late_result(self):
        service = self.service()
        service._sensor_timeout = lambda name:.08
        gate = threading.Event()
        calls = []
        def check(name):
            calls.append(name)
            if name == "body_temperature": gate.wait(2)
            return {"status":"normal"}
        service.sensor_checker.check = check
        try:
            service.request(group="sensors"); service._worker.join(1)
            self.assertFalse(service._worker.is_alive())
            report = service.snapshot()
            self.assertFalse(report["busy"])
            self.assertEqual(report["progress"], 100)
            self.assertEqual(report["results"]["body_temperature"]["status"], "error")
            self.assertEqual(report["results"]["bh1750"]["status"], "normal")
            service.request(group="sensors"); service._worker.join(1)
            report2 = service.snapshot()
            self.assertEqual(calls.count("body_temperature"), 1)
            self.assertEqual(report2["results"]["body_temperature"]["status"], "error")
            revision = report2["revision"]
            gate.set()
            service._sensor_jobs["body_temperature"]["thread"].join(1)
            self.assertEqual(service.snapshot()["revision"], revision)
            self.assertEqual(service.snapshot()["results"]["body_temperature"]["status"], "error")
            service.request(target="body_temperature"); service._worker.join(1)
            self.assertEqual(service.snapshot()["results"]["body_temperature"]["status"], "normal")
        finally:
            gate.set(); service._worker.join(2); service.stop()

    def test_failing_sensor_does_not_leave_other_results_checking(self):
        service=self.service()
        def check(name):
            if name == "smoke": raise OSError("missing module")
            return {"status":"normal"}
        service.sensor_checker.check=check
        service.request(group="sensors"); service._worker.join(1)
        report=service.snapshot()
        self.assertEqual(report["results"]["smoke"]["status"], "error")
        self.assertEqual(report["results"]["body_temperature"]["status"], "normal")
        self.assertFalse(any(r["status"]=="checking" for r in report["results"].values()))
        service.stop()

    def test_shutdown_preserves_resources_until_timed_out_reader_exits(self):
        service=self.service(); service._sensor_timeout=lambda name:.01
        service.manager.config=SimpleNamespace(thread_join_timeout=.02)
        gate=threading.Event()
        def check(name):
            gate.wait(2)
            return {"status":"normal"}
        service.sensor_checker.check=check
        try:
            service.request(target="body_temperature"); service._worker.join(1)
            with self.assertRaisesRegex(RuntimeError,"读取线程"): service.stop()
        finally:
            gate.set(); service._sensor_jobs["body_temperature"]["thread"].join(1)
            service.stop()

    def test_driver_waiting_for_shared_bus_raises_timeout(self):
        bus=SimpleNamespace(lock=threading.RLock(), read_register_bytes=Mock())
        driver=Mlx90614Driver(bus, DEFAULT_CONFIG.body_temperature)
        errors=[]
        def read():
            try: driver.read()
            except Exception as exc: errors.append(exc)
        with bus.lock:
            worker=threading.Thread(target=read); worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
        self.assertIsInstance(errors[0],TimeoutError)
        bus.read_register_bytes.assert_not_called()


if __name__=="__main__":unittest.main()
