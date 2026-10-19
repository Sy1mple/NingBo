"""串口按钮→共享摄像头→网页→模拟讯飞检测→真实临时 SQLite 的完整流程。"""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import build_opener, ProxyHandler

import cv2
import numpy as np
from application import NingBoApplication
from config import DEFAULT_CONFIG
from experiments.access.jetson.cloud_library import CloudFaceLibrary
from experiments.access.jetson.preview import CameraPreview
from hardware.screen.frames import ScreenEventParser


class Screen:
    def __init__(self):
        self.parser, self.input, self.messages = ScreenEventParser(), b'', []
    def start(self): pass
    def stop(self): pass
    def discard_startup_events(self): self.input = b''
    def read_screen_events(self):
        data, self.input = self.input, b''
        return self.parser.feed(data)
    def handle_message(self, message):
        self.messages.append(message)
        return []


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cfg = replace(DEFAULT_CONFIG, face=replace(DEFAULT_CONFIG.face,
                      database_path=str(Path(self.temp.name) / 'faces.sqlite3'),
                      enroll_preview_seconds=0, minimum_sharpness=0, enroll_name='测试人员'))
        self.entered, self.release = threading.Event(), threading.Event()
        self.browser = Mock(return_value=True)
        self.preview = CameraPreview(port=0, open_browser=True, browser_launcher=self.browser)
        self.screen = Screen()
        self.cameras = []
        self.api_calls = []
        owner = self

        class Camera:
            selected_device = '模拟门禁 USB 摄像头'
            def __init__(inner, config):
                inner.calls = []
                owner.cameras.append(inner)
            def start(inner): inner.calls.append(('start', threading.get_ident()))
            def read(inner):
                inner.calls.append(('read', threading.get_ident()))
                return np.random.default_rng(7).integers(0,256,(120,160,3),dtype=np.uint8)
            def close(inner): inner.calls.append(('close', threading.get_ident()))

        class Client:
            def __init__(inner, config): pass
            def detect(inner, jpeg, cancel):
                owner.api_calls.append('detect')
                owner.entered.set()
                while not owner.release.wait(.02):
                    if cancel.is_set(): break
                return {'face_num':1, 'face_1':{'x':10,'y':10,'w':100,'h':100}}
            def compare(inner, first, second, cancel):
                owner.api_calls.append('compare')
                return .99

        devices = SimpleNamespace(config=cfg, start=Mock(), stop=Mock(),
                  emergency_stop=Mock(return_value={'ok':True}),
                  snapshot=Mock(return_value={'sensors':{},'actuators':{}}), control=Mock(),
                  diagnostic_snapshot=Mock(return_value={'revision':0}))
        self.devices = devices
        self.app = NingBoApplication(config=cfg, devices=devices, screen=self.screen,
                                    voice_handler=lambda event: None, camera_preview=self.preview)
        self.app.face.camera_factory = Camera
        self.app.face.cloud_client_factory = Client
        self.addCleanup(self.release.set)
        self.addCleanup(self.app.stop)
        self.app.start()
        self.opener = build_opener(ProxyHandler({}))

    def press(self, address=0x3010, value=0x5010):
        self.screen.input += bytes([0x5a,0xa5,6,0x83,address>>8,address&255,1,value>>8,value&255])
        return self.app.process_once()

    def finish(self):
        self.release.set()
        self.app.face._worker.join(3)
        self.assertFalse(self.app.face._worker.is_alive())
        self.app.process_once()

    def test_idle_http_page_does_not_open_camera_browser_api_or_database(self):
        with self.opener.open(self.preview.url+'status', timeout=2) as response:
            self.assertEqual(json.load(response)['state'], 'idle')
        self.assertEqual(self.cameras, [])
        self.browser.assert_not_called()
        self.assertEqual(self.api_calls, [])
        self.assertFalse(Path(self.app.config.face.database_path).exists())

    def test_correct_wire_button_opens_preview_and_saves_real_local_jpeg(self):
        records = self.press()
        self.assertEqual(records[0]['event'], 'access.face_enroll')
        self.assertTrue(records[0]['handled'])
        self.assertTrue(self.entered.wait(2))
        with self.opener.open(self.preview.url+'stream.mjpg', timeout=2) as response:
            self.assertEqual(response.readline(), b'--frame\r\n')
            response.readline()
            length = int(response.readline().split(b':')[1])
            response.readline()
            jpeg = response.read(length)
            self.assertEqual(cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), 1).shape, (120,160,3))
        self.finish()
        library = CloudFaceLibrary(self.app.config.face.database_path)
        people = library.list_people()
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]['name'], '测试人员')
        stored = library.samples()[0]['jpeg']
        self.assertEqual(cv2.imdecode(np.frombuffer(stored,dtype=np.uint8),1).shape,(120,160,3))
        self.assertEqual(len(self.cameras),1)
        self.assertEqual(len({thread for _,thread in self.cameras[0].calls}),1)
        self.assertEqual(sum(name=='close' for name,_ in self.cameras[0].calls),1)
        self.browser.assert_called_once_with(self.preview.url)
        with self.opener.open(self.preview.url+'status', timeout=2) as response:
            status = json.load(response)
        self.assertEqual(status['state'],'stopped')
        self.assertEqual(status['task']['phase'],'completed')
        self.assertEqual(status['task']['person']['name'],'测试人员')
        self.assertFalse(any(call.args == ('lock',) and call.kwargs.get('on') is True
                             for call in self.devices.control.call_args_list))

    def test_wrong_button_value_never_opens_camera(self):
        records = self.press(value=0x5011)
        self.assertFalse(records[0]['handled'])
        self.assertEqual(self.cameras,[])
        self.assertEqual(self.api_calls,[])

    def test_repeated_click_during_enrollment_is_rejected_without_second_camera(self):
        self.press()
        self.assertTrue(self.entered.wait(2))
        self.assertFalse(self.press()[0]['handled'])
        self.assertEqual(len(self.cameras),1)
        self.finish()

    def test_second_enrollment_keeps_one_person_and_reuses_shared_stream(self):
        self.press()
        self.assertTrue(self.entered.wait(2))
        self.finish()
        stream = self.app.face._camera_stream
        self.press()
        self.finish()
        self.assertIs(self.app.face._camera_stream,stream)
        self.assertEqual(len(self.app.face.library.list_people()),1)
        self.assertTrue(self.app.face.snapshot()['person']['duplicate'])
        self.assertEqual(len(self.cameras),2)

    def test_stop_during_enrollment_does_not_save_late_photo(self):
        self.press()
        self.assertTrue(self.entered.wait(2))
        self.app.face.stop()
        self.assertEqual(self.app.face.library.list_people(),[])
        self.assertEqual(self.app.face.poll_results(),[])
        self.assertFalse(self.app.face._camera_stream.snapshot()['worker_alive'])

    def test_save_failure_shows_error_instead_of_success(self):
        with patch.object(self.app.face.library,'add_photo',side_effect=OSError('模拟磁盘写入失败')):
            self.press()
            self.assertTrue(self.entered.wait(2))
            self.release.set()
            self.app.face._worker.join(3)
        state = self.app.face.snapshot()
        self.assertEqual(state['phase'],'error')
        self.assertIn('磁盘写入失败',state['error'])
        self.assertEqual(self.app.face.library.list_people(),[])
        self.assertFalse(any(r.get('success') for r in self.app.face.poll_results()))
        self.assertEqual(self.preview.hub.snapshot()['task']['phase'],'error')

    def test_stop_during_alignment_preview_does_not_call_cloud_or_save(self):
        self.app.face.config = replace(self.app.face.config, enroll_preview_seconds=1)
        self.press()
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            if self.app.face.snapshot()['note'].startswith('请正对摄像头'):
                break
            time.sleep(.01)
        else:
            self.fail('未进入录入准备预览阶段')
        self.app.face.stop()
        self.assertEqual(self.api_calls,[])
        self.assertEqual(self.app.face.library.list_people(),[])
        self.assertFalse(self.app.face._camera_stream.snapshot()['worker_alive'])


if __name__=='__main__': unittest.main()
