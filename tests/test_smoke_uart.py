"""FFAA 烟雾协议、串口恢复、时间戳与屏幕/诊断链路测试。"""
from dataclasses import replace
import unittest

from JETSON.Sensor import DEFAULT_CONFIG
from JETSON.Sensor.manager import DeviceManager
from JETSON.Sensor.sensors.smoke import SmokeDriver
from JETSON.Sensor.sensors.smoke_protocol import SmokeFrameParser
from JETSON.Sensor.diagnostics.sensor_checks import SensorStatusChecker
from Serial_Screen.device_bridge import ScreenDeviceBridge
from Serial_Screen.screen_service.screen.message_parser import MessageParser
from Serial_Screen.screen_service.screen.screen_sender import ScreenSender
from test_sensor_architecture import fan_only_config


def frame(value):
    return bytes((0xFF, 0xAA, value >> 8, value & 0xFF, 0x3C))


class FakeSerial:
    def __init__(self, payload=b'', **kwargs):
        self.payload = bytearray(payload)
        self.kwargs = kwargs
        self.closed = False
        self.disconnected = False

    @property
    def in_waiting(self):
        if self.disconnected:
            raise OSError('disconnected')
        return len(self.payload)

    def read(self, count):
        data = bytes(self.payload[:count])
        del self.payload[:count]
        return data

    def close(self):
        self.closed = True


class SmokeProtocolTests(unittest.TestCase):
    def test_example_and_every_split_position(self):
        for split in range(1, 5):
            with self.subTest(split=split):
                parser = SmokeFrameParser()
                self.assertEqual(parser.feed(frame(600)[:split]), [])
                self.assertEqual(parser.feed(frame(600)[split:]), [600])
                self.assertEqual(parser.last_frame, bytes.fromhex('FF AA 02 58 3C'))

    def test_consecutive_frames_zero_full_scale_and_embedded_header(self):
        parser = SmokeFrameParser()
        self.assertEqual(parser.feed(frame(0) + frame(65535) + frame(0xFFAA)), [0, 65535, 0xFFAA])
        self.assertEqual(parser.valid_frames, 3)

    def test_noise_repeated_header_bad_tail_and_overlapping_recovery(self):
        parser = SmokeFrameParser()
        self.assertEqual(parser.feed(b'junk\xff\xff\xaa\x00\x01\x00' + frame(600)), [600])
        self.assertEqual(parser.tail_errors, 1)
        self.assertGreater(parser.discarded_bytes, 0)
        parser = SmokeFrameParser()
        self.assertEqual(parser.feed(bytes.fromhex('FF AA FF AA 02 58 3C')), [600])

    def test_parser_storage_is_bounded_and_reconnect_drops_partial_frame(self):
        parser = SmokeFrameParser()
        parser.feed(b'\xff' * 10000)
        self.assertLessEqual(len(parser.buffer), 4)
        parser.feed(b'\xaa\x02')
        parser.reset()
        self.assertEqual(parser.feed(b'\x58\x3c'), [])
        self.assertEqual(parser.feed(frame(600)), [600])


class SmokeDriverTests(unittest.TestCase):
    def setUp(self):
        self.now, self.wall = [0.0], [100.0]
        self.serials = []
        def factory(**kwargs):
            serial = FakeSerial(**kwargs)
            self.serials.append(serial)
            return serial
        self.driver = SmokeDriver(DEFAULT_CONFIG.smoke, serial_factory=factory,
                                  clock=lambda: self.now[0], wall_clock=lambda: self.wall[0])
        self.driver.start()
        self.serial = self.serials[-1]

    def tearDown(self):
        self.driver.close()

    def test_native_concentration_is_not_percent_and_port_is_exclusive(self):
        self.serial.payload.extend(frame(600))
        state = self.driver.read()
        self.assertEqual((state['concentration'], state['status']), (600, 'normal'))
        self.assertIsNone(state['percent'])
        self.assertEqual(state['unit'], 'raw')
        self.assertEqual(self.serial.kwargs['port'], '/dev/ttyTHS0')
        self.assertTrue(self.serial.kwargs['exclusive'])

    def test_partial_frame_waits_then_newest_frame_wins(self):
        self.serial.payload.extend(frame(600)[:3])
        first = self.driver.read()
        self.assertEqual(first['status'], 'unknown')
        self.assertIsNone(first['concentration'])
        self.serial.payload.extend(frame(600)[3:] + frame(700))
        self.assertEqual(self.driver.read()['concentration'], 700)

    def test_missing_bytes_do_not_refresh_sample_time_and_stale_data_expires(self):
        self.serial.payload.extend(frame(0))
        self.assertEqual(self.driver.read()['concentration'], 0)
        self.now[0], self.wall[0] = 3.0, 103.0
        state = self.driver.read()
        self.assertEqual(state['status'], 'normal')
        self.assertEqual(state['sample_time'], 100.0)
        self.now[0], self.wall[0] = 5.01, 105.01
        state = self.driver.read()
        self.assertEqual(state['status'], 'error')
        self.assertIsNone(state['concentration'])
        self.assertFalse(state['online'])

    def test_bytes_without_valid_frame_eventually_report_error(self):
        self.serial.payload.extend(b'not a frame')
        self.assertEqual(self.driver.read()['status'], 'unknown')
        self.now[0] = 6
        state = self.driver.read()
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['valid_frames'], 0)

    def test_disconnect_closes_and_reconnect_never_reuses_previous_measurement(self):
        self.serial.payload.extend(frame(600))
        self.driver.read()
        self.serial.disconnected = True
        with self.assertRaises(OSError):
            self.driver.read()
        self.assertTrue(self.serial.closed)
        state = self.driver.read()
        self.assertEqual(state['status'], 'unknown')
        self.assertIsNone(state['concentration'])
        self.serials[-1].payload.extend(frame(800))
        self.assertEqual(self.driver.read()['concentration'], 800)

    def test_manager_diagnostics_and_screen_preserve_raw_value_and_timestamp(self):
        cfg = fan_only_config()
        cfg = replace(cfg, fan=replace(cfg.fan, enabled=False), smoke=DEFAULT_CONFIG.smoke)
        manager = DeviceManager(config=cfg, sensor_drivers={'smoke': self.driver}, output_drivers={})
        self.serial.payload.extend(frame(600))
        state = manager.poll_sensor('smoke')
        self.assertEqual(state['status'], 'normal')
        self.assertEqual(SensorStatusChecker(manager).check('smoke')['status'], 'normal')
        self.assertEqual(manager.sensors['smoke']['sample_time'], 100.0)
        bridge = ScreenDeviceBridge(None, manager, config=cfg, wall_clock=lambda: self.wall[0])
        values = bridge._scene_values('smoke', manager.snapshot())
        update = MessageParser().parse({'scene': 'smoke', **values})[0]
        self.assertEqual(ScreenSender.build_write_frame(update['address'], update['value']),
                         bytes.fromhex('5A A5 05 82 10 50 02 58'))
        self.now[0], self.wall[0] = 6, 106
        manager.poll_sensor('smoke')
        self.assertEqual(bridge._scene_values('smoke', manager.snapshot()), {})

    def test_screen_overflow_is_explicit_instead_of_clamped(self):
        cfg = fan_only_config()
        manager = DeviceManager(config=cfg, sensor_drivers={}, output_drivers={})
        bridge = ScreenDeviceBridge(None, manager, config=cfg, wall_clock=lambda: 100)
        snapshot = {'sensors': {'smoke': {'protocol': 'ffaa', 'concentration': 65535,
                                        'sample_time': 100, 'status': 'normal'}}}
        with self.assertLogs('Serial_Screen.device_bridge', level='WARNING') as logs:
            self.assertEqual(bridge._scene_values('smoke', snapshot), {})
        self.assertIn('Word', logs.output[0])

    def test_config_rejects_invalid_protocol_buffers_and_timeout(self):
        for values in ({'protocol': 'unknown'}, {'parity': 'E'}, {'read_size': 1},
                       {'read_size': True}, {'stale_after_seconds': 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, smoke=replace(DEFAULT_CONFIG.smoke, **values)).validate()


if __name__ == '__main__':
    unittest.main()
