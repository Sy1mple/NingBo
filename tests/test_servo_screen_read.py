"""只有启动和关闭按钮：目标角度来自配置，回写现有显示 VP。"""
from dataclasses import replace
import unittest
from unittest.mock import patch
from JETSON.Sensor.Config import DEFAULT_CONFIG
from Serial_Screen.device_bridge import ScreenDeviceBridge
from Serial_Screen.screen_service.screen.screen_events import ScreenEventParser
from test_business_bridge import DeviceFixture
from test_device_bridge import MemoryScreen


class ServoButtonTests(unittest.TestCase):
    def setUp(self):
        self.screen = MemoryScreen()
        self.devices = DeviceFixture()
        self.bridge = ScreenDeviceBridge(self.screen, self.devices)

    def dispatch(self, raw):
        return [self.bridge.handle_event(e) for e in ScreenEventParser().feed(bytes.fromhex(raw))]

    def test_start_without_screen_input_starts_sweep_and_displays_first_angle(self):
        response = self.dispatch('5A A5 06 83 30 40 01 50 40')[0]
        self.assertTrue(response['handled'])
        self.assertEqual(self.devices.calls, [('servo', {'sweep': True})])
        self.assertEqual(self.screen.frames, [bytes.fromhex('5A A5 09 82 10 F0 D5 FD D7 AA FF FF'),
                                              bytes.fromhex('5A A5 05 82 10 61 01 C2')])

    def test_config_controls_target_and_display(self):
        cfg = replace(DEFAULT_CONFIG, servo=replace(DEFAULT_CONFIG.servo, sweep_min_angle=60.0))
        bridge = ScreenDeviceBridge(self.screen, self.devices, config=cfg)
        bridge.handle_event('servo.start')
        self.assertEqual(self.devices.calls, [('servo', {'sweep': True})])
        self.assertEqual(self.screen.frames, [bytes.fromhex('5A A5 09 82 10 F0 D5 FD D7 AA FF FF'),
                                              bytes.fromhex('5A A5 05 82 10 61 02 58')])

    def test_display_value_does_not_replace_configured_angle(self):
        self.dispatch('5A A5 06 83 10 61 01 00 00')
        self.bridge.handle_event('servo.start')
        self.assertEqual(self.devices.calls, [('servo', {'sweep': True})])

    def test_stop_only_stops_servo_and_clears_target_display(self):
        self.bridge.handle_event('servo.start')
        self.dispatch('5A A5 06 83 30 50 01 50 50')
        self.assertEqual(self.devices.calls[-1], ('servo', {'stop': True}))
        self.assertEqual(self.screen.frames[-1], bytes.fromhex('5A A5 05 82 10 61 00 00'))
        self.assertEqual(len(self.screen.frames), 4)
        self.bridge.tick()
        self.assertEqual(len(self.screen.frames), 4)
        self.assertEqual(len(self.devices.calls), 2)

    def test_display_failure_stops_servo(self):
        with patch.object(self.screen, 'handle_message', side_effect=OSError('serial error')):
            with self.assertRaises(OSError):
                self.bridge.handle_event('servo.start')
        self.assertEqual(self.devices.calls, [('servo', {'sweep': True}), ('servo', {'stop': True})])

    def test_startup_does_not_move_servo(self):
        self.bridge.reset()
        self.bridge.tick()
        self.assertEqual(self.devices.calls, [])
        self.assertEqual(self.screen.frames, [])

    def test_display_follows_driver_with_rate_limit_and_stop(self):
        state = {'on': True, 'angle': 45.0}
        self.devices.snapshot = lambda: {'actuators': {'servo': state}, 'sensors': {}}
        now = [0.0]
        self.bridge.clock = lambda: now[0]
        self.bridge.handle_event('servo.start')
        state['angle'] = 60
        self.bridge.tick()
        self.assertEqual(self.screen.frames[-1], bytes.fromhex('5A A5 05 82 10 61 02 58'))
        state['angle'] = 90
        now[0] = .1
        self.bridge.tick()
        self.assertEqual(len(self.screen.frames), 4)
        now[0] = .2
        self.bridge.tick()
        self.assertEqual(self.screen.frames[-1], bytes.fromhex('5A A5 05 82 10 61 03 84'))
        self.bridge.handle_event('servo.stop')
        state['angle'] = 135
        now[0] = 1
        self.bridge.tick()
        self.assertEqual(len(self.screen.frames), 8)
        self.assertEqual(self.screen.frames[-1], bytes.fromhex('5A A5 05 82 10 61 00 00'))

    def test_repeated_start_does_not_restart_sweep(self):
        self.bridge.handle_event('servo.start')
        self.bridge.handle_event('servo.start')
        self.assertEqual(self.devices.calls, [('servo', {'sweep': True})])


if __name__ == '__main__':
    unittest.main()
