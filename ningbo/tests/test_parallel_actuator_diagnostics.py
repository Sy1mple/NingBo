"""Independent jobs must publish promptly, and serial verification preserves input."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from hardware.screen.sender import ScreenSender
from screen_daemon import ScreenDaemon
from system.diagnostics.jetson.service import DiagnosticService
from system.diagnostics.bridge import DiagnosticsScreenBridge


class ParallelActuatorTests(unittest.TestCase):
    def service(self):
        return DiagnosticService(SimpleNamespace(_started=True, config=DEFAULT_CONFIG))

    def wait_for(self, predicate):
        end = time.monotonic() + 1
        while not predicate():
            if time.monotonic() >= end:
                self.fail('result did not arrive independently')
            time.sleep(.005)

    def test_lock_finishes_and_updates_screen_while_servo_still_waits(self):
        service = self.service()
        gate = threading.Event()
        started = {name: threading.Event() for name in service.targets('actuators')}
        def check(name, cancel_event=None):
            started[name].set()
            if name == 'servo':
                gate.wait(2)
            return {'status': 'normal'}
        service.actuator_checker.check = check
        screen = SimpleNamespace(handle_message=Mock(return_value=[]))
        clock = [100.]
        bridge = DiagnosticsScreenBridge(screen, SimpleNamespace(
            request_diagnostics=service.request, diagnostic_snapshot=service.snapshot),
            clock=lambda: clock[0])
        try:
            bridge.handle_event('diagnostics.actuators')
            self.assertTrue(all(event.wait(1) for event in started.values()))
            self.wait_for(lambda:service.snapshot()['results']['lock']['status'] == 'normal')
            self.assertEqual(service.snapshot()['results']['servo']['status'], 'checking')
            bridge.tick()
            clock[0] += .6
            bridge.tick()
            self.assertEqual(screen.handle_message.call_args[0][0]['statuses']['lock'], 'normal')
            self.assertEqual(screen.handle_message.call_args[0][0]['statuses']['servo'], 'checking')
        finally:
            gate.set()
            service._worker.join(2)
            service.stop()

    def test_stuck_actuator_times_out_without_late_result_or_duplicate_job(self):
        service = self.service()
        service._check_timeout = lambda name: .05
        gate = threading.Event()
        calls = []
        cancel_events = []
        def check(name, cancel_event=None):
            calls.append(name)
            if name == 'servo':
                cancel_events.append(cancel_event)
                gate.wait(2)
            return {'status': 'normal'}
        service.actuator_checker.check = check
        try:
            service.request(group='actuators')
            service._worker.join(1)
            report = service.snapshot()
            self.assertFalse(report['busy'])
            self.assertEqual(report['results']['servo']['status'], 'error')
            self.assertEqual(report['results']['lock']['status'], 'normal')
            self.assertTrue(cancel_events[0].is_set())
            service.request(group='actuators')
            service._worker.join(1)
            self.assertEqual(calls.count('servo'), 1)
            revision = service.snapshot()['revision']
            gate.set()
            service._actuator_jobs['servo']['thread'].join(1)
            self.assertEqual(service.snapshot()['revision'], revision)
            self.assertEqual(service.snapshot()['results']['servo']['status'], 'error')
        finally:
            gate.set()
            service.stop()

    def test_actuators_start_while_absent_smoke_is_still_waiting(self):
        service = self.service()
        gate = threading.Event()
        service.sensor_checker.check = lambda name: (gate.wait(2) if name == 'smoke' else None) or {'status':'normal'}
        service.actuator_checker.check = lambda name, cancel_event=None: {'status':'normal'}
        try:
            service.request()
            self.wait_for(lambda:service.snapshot()['results']['lock']['status'] == 'normal')
            self.assertEqual(service.snapshot()['results']['smoke']['status'], 'checking')
        finally:
            gate.set()
            service._worker.join(2)
            service.stop()

    def test_device_sessions_overlap_without_holding_global_lock(self):
        manager = DeviceManager(sensor_drivers={}, output_drivers={})
        manager._started = True
        gate = threading.Event()
        entered = threading.Event()
        def other():
            with manager.diagnostic_session('fan'):
                entered.set()
                gate.wait(1)
        with manager.diagnostic_session('lock'):
            worker = threading.Thread(target=other)
            worker.start()
            try:
                self.assertTrue(entered.wait(.5))
                self.assertTrue(manager._control_lock.acquire(timeout=.1))
                manager._control_lock.release()
            finally:
                gate.set()
                worker.join(1)
        self.assertFalse(manager._diagnostic_owners)

    def test_same_device_control_is_rejected_without_blocking_ui(self):
        manager = DeviceManager(sensor_drivers={}, output_drivers={})
        manager._started = True
        entered, release = threading.Event(), threading.Event()
        def hold():
            with manager.diagnostic_session('lock'):
                entered.set()
                release.wait(1)
        worker = threading.Thread(target=hold)
        worker.start()
        try:
            self.assertTrue(entered.wait(.5))
            with self.assertRaisesRegex(RuntimeError, '独立状态检测'):
                manager._assert_actuator_available('lock')
            manager._assert_actuator_available('fan')
        finally:
            release.set()
            worker.join(1)


class StatusDeliveryTests(unittest.TestCase):
    def test_readback_keeps_button_bytes_and_restores_timeout(self):
        button = bytes.fromhex('5aa506839002018002')
        reply = bytes.fromhex('5aa50683601001bac3')
        class Serial:
            timeout = .5
            is_open = True
            def __init__(self): self.data = button + reply
            @property
            def in_waiting(self): return len(self.data)
            def write(self, data): return len(data)
            def read(self, count):
                data, self.data = self.data[:count], self.data[count:]
                return data
        sender = ScreenSender()
        sender.ser = Serial()
        self.assertEqual(sender.read_value(0x6010), 0xBAC3)
        self.assertEqual(sender.take_pending_input(), button)
        self.assertEqual(sender.ser.timeout, .5)

    def test_mismatch_retries_then_confirms_real_vp_value(self):
        now = [100.]
        sender = SimpleNamespace(read_value=Mock(side_effect=[0xBCEC, 0xBAC3]), _write_frame=Mock())
        daemon = ScreenDaemon(SimpleNamespace(sender=sender), clock=lambda:now[0])
        daemon._lock_status_pending = {'frame':b'frame', 'value':0xBAC3, 'attempts':0, 'next':100.}
        daemon._verify_lock_status()
        sender._write_frame.assert_called_once_with(b'frame')
        now[0] += .3
        daemon._verify_lock_status()
        self.assertIsNone(daemon._lock_status_pending)
        self.assertEqual(daemon._lock_status_verified, 0xBAC3)

    def test_final_result_is_refreshed_twice_then_stops(self):
        now = [100.]
        screen = SimpleNamespace(handle_message=Mock(return_value=[]))
        report = {'revision':1, 'busy':False, 'progress':100, 'overall':'normal',
                  'results':{'lock':{'status':'normal'}}}
        bridge = DiagnosticsScreenBridge(screen, SimpleNamespace(diagnostic_snapshot=lambda:report),
                                         clock=lambda:now[0])
        bridge.tick()
        for _ in range(4):
            now[0] += 1
            bridge.tick()
        self.assertEqual(screen.handle_message.call_count, 3)


if __name__ == '__main__':
    unittest.main()
