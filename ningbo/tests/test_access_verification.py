"""屏幕原始帧→模拟讯飞→真实 GPIO 驱动，验证一次认证及两秒执行器联动。"""
from dataclasses import replace
import unittest
from unittest.mock import patch

import test_access_enrollment as enrollment
from test_startup_gpio import GpioTrace
from device_manager import DeviceManager
from device_service import DeviceService
from experiments.access.jetson.cloud_library import CloudFaceLibrary
from pathlib import Path


class VerificationTests(unittest.TestCase):
    press = enrollment.EnrollmentTests.press
    finish = enrollment.EnrollmentTests.finish

    def setUp(self):
        enrollment.EnrollmentTests.setUp(self)
        self.now = 100.0
        access = self.app.bridge.access
        for controller in (access, access.lock_control, access.failure_alarm):
            controller.clock = lambda: self.now
        self.app.face.config = replace(self.app.face.config, cloud_round_interval=1)
        config = replace(self.app.config, current=replace(self.app.config.current, enabled=False),
                         ads1115=replace(self.app.config.ads1115, enabled=False))
        self.gpio = GpioTrace()
        manager = DeviceManager(config=config, gpio=self.gpio, sensor_drivers={})
        self.hardware = DeviceService(manager=manager)
        self.hardware.start()
        self.addCleanup(self.hardware.stop)
        self.addCleanup(self.app.stop)
        self.devices.control.side_effect = self.hardware.control
        self.devices.snapshot.side_effect = self.hardware.snapshot
        self.app.face.library.initialize()
        self.app.face.library.add_photo(b'local-reference-photo', '已录入人员')
        self.assertEqual(self.gpio.levels, {29: 0, 31: 0, 12: 0, 35: 0, 38: 0, 40: 1, 13: 0, 16: 0, 18: 0, 22: 0})
        # 后续断言只记录按钮产生的动作，启动时已验证全部静态输出关闭。
        self.gpio.calls.clear()

    def verify(self, score=.99, error=None):
        owner = self
        class Client:
            def __init__(self, config): pass
            def detect(self, jpeg, cancel):
                owner.api_calls.append('detect')
                owner.entered.set()
                while not owner.release.wait(.01):
                    if cancel.is_set(): break
                if error: raise RuntimeError(error)
                return {'face_num': 1, 'face_1': {'x':10, 'y':10, 'w':100, 'h':100}}
            def compare(self, current, stored, cancel):
                owner.assertEqual(stored, b'local-reference-photo')
                owner.assertTrue(current.startswith(b'\xff\xd8'))
                owner.api_calls.append('compare')
                return score
        self.app.face.cloud_client_factory = Client
        return self.press(0x3020, 0x5020)

    def outputs(self, device):
        return [call.kwargs['on'] for call in self.devices.control.call_args_list
                if call.args == (device,)]

    def assert_rgb(self, color):
        self.assertEqual(self.hardware.snapshot()['actuators']['rgb']['color'], color)
        levels = {'off': (0, 0, 0), 'red': (1, 0, 0),
                  'green': (0, 1, 0), 'blue': (0, 0, 1)}
        self.assertEqual(tuple(self.gpio.levels[pin] for pin in (12, 35, 38)), levels[color])

    def test_success_uses_local_photo_and_powers_lock_for_two_seconds_once(self):
        result = self.verify()
        self.assertEqual(result[0]['event'], 'access.start')
        self.assertTrue(result[0]['handled'])
        self.finish()
        self.assertEqual(self.api_calls, ['detect','compare','detect','compare'])
        self.assertEqual(self.outputs('lock'), [True])
        self.assertEqual(self.outputs('buzzer'), [])
        self.assertEqual(self.gpio.levels[29], 1)
        self.assert_rgb('green')
        self.assertEqual(self.app.face.snapshot()['phase'], 'completed')
        self.assertFalse(self.app.bridge.access.enabled)
        self.assertFalse(self.app.face._camera_stream.snapshot()['worker_alive'])
        self.assertFalse(self.press(0x3020,0x5020)[0]['handled'])
        self.now += 1.99
        self.app.process_once()
        self.assertEqual(self.outputs('lock'), [True])
        self.assert_rgb('green')
        self.now += .02
        for _ in range(5): self.app.process_once()
        self.assertEqual(self.outputs('lock'), [True,False])
        self.assertEqual(self.gpio.levels[29], 0)
        self.assert_rgb('off')
        self.assertNotIn('access', self.app.bridge.feedback.results)
        self.assertEqual(self.api_calls, ['detect','compare','detect','compare'])
        self.assertTrue(self.verify()[0]['handled'])
        self.finish()
        self.assertEqual(self.outputs('lock'), [True,False,True])

    def test_mismatch_buzzes_for_two_seconds_and_never_unlocks(self):
        self.verify(.2)
        self.finish()
        self.assertEqual(self.api_calls, ['detect','compare'])
        self.assertEqual(self.outputs('lock'), [])
        self.assertEqual(self.outputs('buzzer'), [True])
        self.assertEqual(self.gpio.levels[40], 0)  # 已配置的低电平有效蜂鸣器。
        self.assert_rgb('red')
        self.now += 1.99
        self.app.process_once()
        self.assertEqual(self.outputs('buzzer'), [True])
        self.assert_rgb('red')
        self.now += .02
        for _ in range(5): self.app.process_once()
        self.assertEqual(self.outputs('buzzer'), [True,False])
        self.assertEqual(self.gpio.levels[40], 1)
        self.assert_rgb('off')
        self.assertNotIn('access', self.app.bridge.feedback.results)
        self.assertFalse(self.app.face.snapshot()['recognized'])

    def test_api_error_alarms_only_once_without_unlocking(self):
        self.verify(error='免费次数不足')
        self.finish()
        self.assertEqual(self.outputs('lock'), [])
        self.assertEqual(self.outputs('buzzer'), [True])
        self.assert_rgb('red')
        self.now += 2.01
        for _ in range(5): self.app.process_once()
        self.assertEqual(self.outputs('buzzer'), [True,False])
        self.assert_rgb('off')
        self.assertIn('免费次数不足',self.app.face.snapshot()['error'])

    def test_expiring_verification_releases_only_its_own_rgb_source(self):
        self.app.bridge.feedback.activate('ultrasonic')
        self.verify(.2)
        self.finish()
        self.assert_rgb('red')
        self.now += 2.01
        self.app.process_once()
        self.assert_rgb('blue')
        self.assertNotIn('access', self.app.bridge.feedback.results)
        self.assertIn('ultrasonic', self.app.bridge.feedback.results)

    def test_new_enrollment_is_not_expired_by_previous_verification_timer(self):
        self.verify()
        self.finish()
        self.assert_rgb('green')
        self.now += 1.0
        self.release.clear()
        self.assertTrue(self.press()[0]['handled'])
        self.assertTrue(self.entered.wait(2))
        self.assert_rgb('blue')
        self.now += 1.01
        self.app.process_once()
        self.assert_rgb('blue')
        self.assertIn('face_enroll', self.app.bridge.feedback.results)
        self.finish()

    def test_wrong_value_has_no_camera_api_or_output(self):
        self.assertFalse(self.press(0x3020,0x5021)[0]['handled'])
        self.assertEqual(self.cameras, [])
        self.assertEqual(self.api_calls, [])
        self.assertEqual(self.gpio.calls, [])

    def test_repeated_click_and_cancel_do_not_apply_late_success(self):
        self.verify()
        self.assertTrue(self.entered.wait(2))
        self.assertFalse(self.press(0x3020,0x5020)[0]['handled'])
        self.app.bridge.access.handle_event('access.stop')
        self.app.process_once()
        self.assertEqual(self.outputs('lock'), [])
        self.assertEqual(self.outputs('buzzer'), [])
        self.assertFalse(self.app.face._camera_stream.snapshot()['worker_alive'])

    def test_empty_library_fails_without_camera_or_api(self):
        self.app.face.library = CloudFaceLibrary(str(Path(self.temp.name) / 'empty.sqlite3'))
        self.verify()
        self.finish()
        self.assertEqual(self.cameras, [])
        self.assertEqual(self.api_calls, [])
        self.assertEqual(self.outputs('lock'), [])
        self.assertEqual(self.outputs('buzzer'), [True])

    def test_timeout_fails_and_stops_camera(self):
        self.app.face.config = replace(self.app.face.config, verification_timeout=.01)
        with patch('experiments.access.jetson.cloud_service.capture', return_value=(None,'等待人脸',0)):
            self.verify()
            self.finish()
        self.assertIn('超时',self.app.face.snapshot()['note'])
        self.assertEqual(self.outputs('lock'), [])
        self.assertEqual(self.outputs('buzzer'), [True])
        self.assertFalse(self.app.face._camera_stream.snapshot()['worker_alive'])

    def test_ultrasonic_stop_closes_buzzer_without_waiting_for_access(self):
        self.verify(.2)
        self.finish()
        self.assertEqual(self.outputs('buzzer'), [True])
        self.app.bridge.proximity_alarm.activate()
        self.app.bridge.handle_event('ultrasonic.stop')
        self.assertEqual(self.outputs('buzzer'), [True,False])
        self.assertEqual(self.gpio.levels[40], 1)
        self.assertFalse(self.app.bridge.proximity_alarm.active)
        self.assertTrue(self.app.bridge.access.failure_alarm.active)
        self.assertEqual(self.outputs('lock'), [])

    def test_access_failure_alarm_drives_buzzer_without_proximity_flush(self):
        alarm = self.app.bridge.access.failure_alarm
        alarm.result(False)
        self.assertEqual(self.outputs('buzzer'), [True])
        self.assertFalse(self.app.bridge.proximity_alarm.active)
        self.now += 2.01
        alarm.tick()
        self.assertEqual(self.outputs('buzzer'), [True,False])
        self.assertEqual(self.outputs('lock'), [])

    def test_completion_between_state_and_queue_read_does_not_lose_unlock(self):
        access = self.app.bridge.access
        access.enabled, access.session_id = True, 7
        def completing_snapshot():
            self.app.face.complete_recognition(0, 7, dict(recognized=True, person_id=1,
                name='已录入人员', score=.99, note='已匹配库内人员'))
            return dict(phase='completed',mode='recognize',error='')
        with patch.object(self.app.face,'snapshot',side_effect=completing_snapshot):
            access.tick()
        self.assertEqual(self.outputs('lock'),[True])
        self.assertFalse(access.enabled)

    def test_lock_off_failure_is_retried_next_loop(self):
        self.verify()
        self.finish()
        failed = []
        def control(device, **kwargs):
            if device == 'lock' and not kwargs['on'] and not failed:
                failed.append(True)
                raise OSError('模拟断电写入失败')
            return self.hardware.control(device, **kwargs)
        self.devices.control.side_effect = control
        self.now += 2.01
        with self.assertLogs('ningbo',level='ERROR'):
            self.app.process_once()
        self.assertEqual(self.gpio.levels[29],1)
        self.app.process_once()
        self.assertEqual(self.gpio.levels[29],0)
        self.assertFalse(self.app.bridge.access.lock_control.active)

    def test_stop_clears_buzzer_before_deadline(self):
        self.verify(.2)
        self.finish()
        self.assertEqual(self.outputs('buzzer'),[True])
        self.assert_rgb('red')
        self.app.bridge.handle_event('access.stop')
        self.assertEqual(self.outputs('buzzer'),[True,False])
        self.assertFalse(self.app.bridge.access.failure_alarm.active)
        self.assert_rgb('off')
        self.assertIsNone(self.app.bridge.access._verification_until)


if __name__ == '__main__': unittest.main()
