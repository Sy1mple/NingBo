"""USB renumbering, wrong devices, incomplete replies and capture-node selection."""
from dataclasses import replace
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Sensor.sensors import usb_camera
from JETSON.Sensor.sensors.usb_microphone import UsbMicrophone, select_audio_device
from Serial_Screen.screen_service.screen import serial_discovery
from Serial_Screen.screen_service.screen.service import ScreenService

BUTTON = bytes.fromhex('5A A5 06 83 20 00 01 40 00')


class PortFixture:
    def __init__(self, owner, **settings):
        self.owner, self.settings = owner, settings
        self.port = None
        self.chunks = []
        self.writes = []
        self.is_open = False
        self.closed = False
        self.timeout = settings['timeout']

    def open(self):
        if self.owner.roles[self.port] == 'busy':
            raise OSError('busy')
        self.is_open = True

    @property
    def in_waiting(self):
        return sum(len(part) for part in self.chunks)

    def write(self, query):
        self.writes.append(query)
        role = self.owner.roles[self.port]
        if role == 'screen':
            reply = bytes.fromhex('5A A5 06 83') + query[4:6] + b'\x01\x00\x7b'
            if self.owner.button and len(self.writes) == 1:
                self.chunks.append(BUTTON)
            self.chunks.extend((reply[:1], reply[1:4], reply[4:]))
        elif role == 'echo':
            self.chunks.append(query)
        elif role == 'wrong_address':
            self.chunks.append(bytes.fromhex('5A A5 06 83 99 99 01 00 00'))
        elif role == 'partial':
            self.chunks.append(bytes.fromhex('5A A5 06 83') + query[4:6] + b'\x01')
        else:
            self.chunks.append(b'[1234] NO FRAME ok=0\n')
        return len(query)

    def read(self, size):
        if not self.chunks:
            return b''
        part = self.chunks.pop(0)
        if len(part) > size:
            self.chunks.insert(0, part[size:])
        return part[:size]

    def close(self):
        self.closed = True
        self.is_open = False


class SerialFactory:
    def __init__(self, roles, button=False):
        self.roles, self.button, self.instances = roles, button, []

    def __call__(self, **kwargs):
        port = PortFixture(self, **kwargs)
        self.instances.append(port)
        return port


def port_info(name, vid=0x1A86):
    return SimpleNamespace(device=name, vid=vid)


class ScreenDiscoveryTests(unittest.TestCase):
    def discover(self, roles, **kwargs):
        factory = SerialFactory(roles, button=kwargs.pop('button', False))
        infos = kwargs.pop('port_infos', [port_info(name) for name in roles])
        config = replace(DEFAULT_CONFIG.screen, auto_probe_timeout=.003, auto_probe_attempts=1)
        port, pending = serial_discovery.discover_screen(config, port_infos=infos, serial_factory=factory, **kwargs)
        self.addCleanup(port.close)
        return port, pending, factory

    def test_protocol_selects_screen_after_usb_indices_swap(self):
        for screen, other in (('/dev/ttyUSB0', '/dev/ttyUSB1'), ('/dev/ttyUSB7', '/dev/ttyUSB0')):
            with self.subTest(screen=screen):
                port, pending, factory = self.discover({other: 'ascii', screen: 'screen'})
                self.assertEqual(port.port, screen)
                self.assertEqual(pending, b'')
                self.assertEqual(port.writes, [bytes.fromhex('5A A5 04 83 10 00 01'), bytes.fromhex('5A A5 04 83 10 30 01')])
                self.assertTrue(all(p.closed for p in factory.instances if p is not port))
                self.assertFalse(port.dtr)
                self.assertFalse(port.rts)

    def test_ascii_echo_wrong_vp_and_truncated_reply_are_not_screens(self):
        for role in ('ascii', 'echo', 'wrong_address', 'partial', 'busy'):
            with self.subTest(role=role):
                factory = SerialFactory({'/dev/ttyUSB0': role})
                config = replace(DEFAULT_CONFIG.screen, auto_probe_timeout=.003, auto_probe_attempts=1)
                with self.assertRaisesRegex(OSError, '未发现'):
                    serial_discovery.discover_screen(config, port_infos=[port_info('/dev/ttyUSB0')], serial_factory=factory)
                self.assertTrue(all(port.closed for port in factory.instances))

    def test_known_peripheral_and_internal_uart_are_not_opened(self):
        port, _, factory = self.discover({'/dev/ttyUSB0': 'screen', '/dev/ttyUSB2': 'screen'},
            excluded_ports=('/dev/ttyUSB2',), port_infos=[port_info('/dev/ttyUSB0'), port_info('/dev/ttyUSB2'), port_info('/dev/ttyTHS0', None)])
        self.assertEqual(port.port, '/dev/ttyUSB0')
        self.assertEqual(len(factory.instances), 1)

    def test_same_physical_port_is_not_probed_twice(self):
        with patch.object(serial_discovery.Path, 'resolve', return_value=Path('/one-device')):
            _, _, factory = self.discover({'/dev/ttyUSB0': 'screen'},
                port_infos=[port_info('/dev/ttyUSB0'), port_info('/dev/alias')])
        self.assertEqual(len(factory.instances), 1)

    def test_multiple_screens_are_closed_and_require_explicit_choice(self):
        factory = SerialFactory({'/dev/ttyUSB0': 'screen', '/dev/ttyUSB1': 'screen'})
        with self.assertRaisesRegex(OSError, '多块'):
            serial_discovery.discover_screen(DEFAULT_CONFIG.screen,
                port_infos=[port_info(name) for name in factory.roles], serial_factory=factory)
        self.assertTrue(all(port.closed for port in factory.instances))

    def test_startup_button_survives_probe_and_reaches_scene_parser(self):
        port, pending, _ = self.discover({'/dev/ttyUSB0': 'screen'}, button=True)
        screen = ScreenService()
        with patch('Serial_Screen.screen_service.screen.screen_sender.discover_screen', return_value=(port, pending)):
            screen.start()
        self.assertEqual([e['event'] for e in screen.read_screen_events()], ['environment.start'])
        self.assertEqual(screen.read_screen_events(), [])
        screen.stop()


class CameraDiscoveryTests(unittest.TestCase):
    def module(self, reads):
        module = MagicMock()
        capture = module.VideoCapture.return_value
        capture.isOpened.return_value = True
        capture.read.side_effect = reads
        return module, capture

    def test_video_renumbering_and_metadata_node_filter(self):
        frame = np.zeros((480, 640, 3), np.uint8)
        nodes = [{'path': '/dev/video8', 'bus': 'usb-one', 'usb': True, 'capture': False},
                 {'path': '/dev/video9', 'bus': 'usb-one', 'usb': True, 'capture': True},
                 {'path': '/dev/video0', 'bus': 'platform-internal', 'usb': False, 'capture': True}]
        module, capture = self.module([(True, frame), (False, None)])
        camera = usb_camera.UsbCamera(DEFAULT_CONFIG.camera, module)
        with patch.object(usb_camera, 'camera_nodes', return_value=nodes):
            camera.start()
        self.assertEqual(module.VideoCapture.call_args.args[0], '/dev/video9')
        self.assertIs(camera.read(), frame)
        with self.assertRaises(OSError):
            camera.read()
        camera.close()
        capture.release.assert_called_once()

    def test_bad_first_frame_is_released_and_other_node_is_tried(self):
        module = MagicMock()
        bad, good = MagicMock(), MagicMock()
        bad.read.return_value = (False, None)
        frame = np.zeros((8, 8, 3), np.uint8)
        good.read.return_value = (True, frame)
        module.VideoCapture.side_effect = [bad, good]
        nodes = [dict(path=path, bus='usb-one', usb=True, capture=True) for path in ('/dev/video6', '/dev/video7')]
        camera = usb_camera.UsbCamera(DEFAULT_CONFIG.camera, module)
        with patch.object(usb_camera, 'camera_nodes', return_value=nodes):
            camera.start()
        bad.release.assert_called_once()
        self.assertEqual(camera.selected_device, '/dev/video7')
        camera.close()
        good.release.assert_called_once()

    def test_no_camera_and_multiple_physical_cameras_are_not_arbitrarily_selected(self):
        for nodes in ([], [dict(path='/dev/video1', bus='usb-one', usb=True, capture=True),
                           dict(path='/dev/video2', bus='usb-two', usb=True, capture=True)]):
            module = MagicMock()
            with patch.object(usb_camera, 'camera_nodes', return_value=nodes), self.assertRaises(OSError):
                usb_camera.UsbCamera(DEFAULT_CONFIG.camera, module).start()
            module.VideoCapture.assert_not_called()

    def test_querycap_uses_node_caps_instead_of_whole_camera_caps(self):
        packed = struct.pack('=16s32s32sIII3I', b'uvcvideo', b'USB Camera', b'usb-one', 1,
                             0x80000001, 0x00800000, 0, 0, 0)
        def ioctl(fd, request, data, mutate):
            data[:] = packed
        with patch.dict(sys.modules, {'fcntl': SimpleNamespace(ioctl=ioctl)}), \
             patch.object(usb_camera.os, 'O_NONBLOCK', 2048, create=True), \
             patch.object(usb_camera.os, 'open', return_value=123), patch.object(usb_camera.os, 'close') as close:
            info = usb_camera._capabilities('/dev/video5')
        self.assertTrue(info['usb'])
        self.assertFalse(info['capture'])
        close.assert_called_once_with(123)


class AudioDiscoveryTests(unittest.TestCase):
    def module(self, devices):
        return SimpleNamespace(query_devices=lambda: devices, check_input_settings=MagicMock())

    def test_usb_mic_and_speaker_follow_name_after_soundcard_indices_change(self):
        usb = dict(name='AIMIC-M4: USB Audio (hw:7,0)', max_input_channels=2,
                   max_output_channels=2, default_samplerate=48000)
        internal = dict(name='NVIDIA internal', max_input_channels=16, max_output_channels=16, default_samplerate=48000)
        for devices, expected in (([internal, usb], 1), ([usb, internal], 0)):
            module = self.module(devices)
            audio = UsbMicrophone(DEFAULT_CONFIG.voice, module)
            audio.prepare()
            self.assertEqual(audio.device_index, expected)
            self.assertEqual(audio.output_device_index, expected)
            self.assertEqual(select_audio_device('auto', module, 'output')['index'], expected)

    def test_auto_prefers_known_usb_and_never_falls_back_to_internal_audio(self):
        internal = dict(name='NVIDIA internal', max_input_channels=16)
        generic = dict(name='Generic USB Audio', max_input_channels=1)
        known = dict(name='AISPEECH', max_input_channels=1)
        self.assertEqual(select_audio_device('auto', self.module([internal, generic, known]))['index'], 2)
        self.assertEqual(select_audio_device('auto', self.module([internal, generic]))['index'], 1)
        with self.assertRaisesRegex(RuntimeError, '0 个设备'):
            select_audio_device('auto', self.module([internal]))
        with self.assertRaisesRegex(RuntimeError, '2 个设备'):
            select_audio_device('auto', self.module([generic, generic]))


if __name__ == '__main__':
    unittest.main()
