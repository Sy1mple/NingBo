"""模拟相机 + 真实本机 HTTP 验证；不打开 USB 摄像头、GPIO、Firefox 或 SSH。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

import cv2
import numpy as np
from experiments.access.jetson.camera_stream import CameraStream
from experiments.access.jetson.config import CameraConfig
from experiments.access.jetson.desktop_browser import local_desktop_environment, launch_local_firefox
from experiments.access.jetson.preview import CameraPreview, FrameHub


class FakeCamera:
    def __init__(self, config):
        self.config = config
        self.selected_device = '模拟 USB 摄像头'
        self.calls = []
        self.frame = np.zeros((120, 160, 3), dtype=np.uint8)
        self.frame[:, :, 1] = np.arange(160, dtype=np.uint8)
    def start(self): self.calls.append(('start', threading.get_ident()))
    def read(self):
        self.calls.append(('read', threading.get_ident()))
        return self.frame.copy()
    def close(self): self.calls.append(('close', threading.get_ident()))


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.preview = CameraPreview(port=0, open_browser=False)
        self.preview.start()
        self.addCleanup(self.preview.stop)
        self.opener = build_opener(ProxyHandler({}))
        self.camera = FakeCamera(CameraConfig())
        self.stream = CameraStream(CameraConfig(), self.preview.hub, camera_factory=lambda cfg: self.camera)
        self.addCleanup(self.stream.close)

    def get(self, path):
        return self.opener.open(self.preview.url + path, timeout=2)

    def test_server_alone_never_opens_camera_or_serves_project_files(self):
        with self.get('') as response:
            self.assertIn('摄像头实时预览', response.read().decode())
        with self.get('status') as response:
            self.assertEqual(json.load(response)['state'], 'idle')
        self.assertEqual(self.camera.calls, [])
        self.assertEqual(self.preview.server.server_address[0], '127.0.0.1')
        for path in ('.env.face', '../.env.face', 'stream.mjpg'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.get(path)
            self.assertIn(caught.exception.code, (404, 503))

    def test_two_browsers_share_one_camera_and_receive_real_jpeg(self):
        self.stream.start()
        def receive():
            with self.get('stream.mjpg') as response:
                self.assertIn('multipart/x-mixed-replace', response.headers['Content-Type'])
                self.assertEqual(response.readline(), b'--frame\r\n')
                self.assertEqual(response.readline(), b'Content-Type: image/jpeg\r\n')
                length = int(response.readline().split(b':')[1])
                self.assertEqual(response.readline(), b'\r\n')
                jpeg = response.read(length)
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                return frame.shape
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: receive(), range(2)))
        self.assertEqual(results, [(120, 160, 3)] * 2)
        self.assertEqual(sum(c[0] == 'start' for c in self.camera.calls), 1)
        self.assertTrue(self.stream.snapshot()['worker_alive'])

    def test_open_read_close_are_in_same_worker_and_restart_is_clean(self):
        main_thread = threading.get_ident()
        self.stream.start()
        self.stream.start()
        frame = self.stream.read()
        frame[:] = 255
        self.assertFalse(np.all(self.stream.read() == 255))
        self.stream.close()
        identifiers = {ident for _, ident in self.camera.calls}
        self.assertEqual(len(identifiers), 1)
        self.assertNotIn(main_thread, identifiers)
        self.assertEqual(sum(c[0] == 'start' for c in self.camera.calls), 1)
        self.assertEqual(sum(c[0] == 'close' for c in self.camera.calls), 1)
        self.assertIsNone(self.preview.hub.jpeg)
        with self.get('status') as response:
            self.assertEqual(json.load(response)['state'], 'stopped')
        self.stream.start()
        self.assertEqual(sum(c[0] == 'start' for c in self.camera.calls), 2)

    def test_browser_disconnect_does_not_stop_capture(self):
        self.stream.start()
        with self.get('stream.mjpg') as response:
            response.readline()
        self.assertTrue(self.stream.snapshot()['worker_alive'])
        self.assertIsNotNone(self.stream.read())

    def test_camera_failure_clears_last_frame_and_is_visible_to_browser(self):
        release = threading.Event()
        original = self.camera.read
        def read():
            if any(c[0] == 'read' for c in self.camera.calls):
                release.wait(2)
                raise OSError('模拟 USB 已断开')
            return original()
        self.camera.read = read
        self.stream.start()
        release.set()
        self.stream._worker.join(2)
        self.assertIn('USB', self.stream.snapshot()['error'])
        self.assertIsNone(self.preview.hub.jpeg)
        with self.get('status') as response:
            self.assertEqual(json.load(response)['state'], 'error')
        with self.assertRaises(RuntimeError): self.stream.read()

    def test_blocked_read_prevents_second_camera_open_after_stop_timeout(self):
        entered, release = threading.Event(), threading.Event()
        original = self.camera.read
        def read():
            if any(c[0] == 'read' for c in self.camera.calls):
                entered.set()
                release.wait(2)
            return original()
        self.camera.read = read
        self.stream.join_timeout = .02
        self.stream.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(RuntimeError): self.stream.close()
            with self.assertRaises(RuntimeError): self.stream.start()
            self.assertEqual(sum(c[0] == 'start' for c in self.camera.calls), 1)
            self.assertIsNone(self.preview.hub.jpeg)
        finally:
            release.set()
            self.stream._worker.join(2)

    def test_preview_browser_request_is_sent_only_once(self):
        called = threading.Event()
        launcher = Mock(side_effect=lambda url: called.set() or True)
        preview = CameraPreview(port=0, open_browser=True, browser_launcher=launcher)
        try:
            preview.start()
            self.assertTrue(called.wait(1))
            preview.start()
            self.assertEqual(launcher.call_count, 1)
        finally:
            preview.stop()

    def test_stale_frame_is_not_reported_live(self):
        clock = [0]
        hub = FrameHub(clock=lambda: clock[0], stale_seconds=1)
        hub.publish(b'jpeg', 10, 10)
        clock[0] = 2
        self.assertEqual(hub.snapshot()['state'], 'stalled')
        self.assertIsNone(hub.wait_frame())


class DesktopTests(unittest.TestCase):
    @patch('experiments.access.jetson.desktop_browser.sys.platform', 'linux')
    def test_local_desktop_uses_firefox_without_shell(self):
        process = Mock()
        process.wait.return_value = 0
        popen = Mock(return_value=process)
        self.assertTrue(launch_local_firefox('http://127.0.0.1:8765/', environ={'DISPLAY': ':0'},
                                            popen=popen, which=lambda name: '/usr/bin/firefox'))
        args = popen.call_args.args[0]
        self.assertEqual(args, ['/usr/bin/firefox', '--new-window', 'http://127.0.0.1:8765/'])
        self.assertEqual(popen.call_args.kwargs['env']['DISPLAY'], ':0')

    @patch('experiments.access.jetson.desktop_browser.sys.platform', 'linux')
    def test_ssh_uses_current_users_local_desktop_environment(self):
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout='DISPLAY=:0\nXAUTHORITY=/tmp/local-xauth\n'))
        env = local_desktop_environment({'DISPLAY': 'localhost:10.0', 'XAUTHORITY': '/tmp/ssh-xauth'}, run)
        self.assertEqual(env['DISPLAY'], ':0')
        self.assertEqual(env['XAUTHORITY'], '/tmp/local-xauth')

    @patch('experiments.access.jetson.desktop_browser.sys.platform', 'linux')
    def test_no_graphical_session_does_not_launch_browser(self):
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout=''))
        popen = Mock()
        with patch('experiments.access.jetson.desktop_browser.os.getuid', return_value=1000, create=True):
            self.assertIsNone(launch_local_firefox('http://127.0.0.1:8765/', environ={}, run=run, popen=popen))
        popen.assert_not_called()


class CommandTests(unittest.TestCase):
    def test_preview_config_check_opens_nothing(self):
        import camera_preview
        with patch.object(camera_preview, 'CameraPreview') as preview, \
                patch.object(camera_preview, 'CameraStream') as stream, patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(camera_preview.main(['--check-config']), 0)
        preview.assert_not_called()
        stream.assert_not_called()

    def test_failed_camera_start_closes_both_resources(self):
        import camera_preview
        preview, stream = Mock(), Mock()
        preview.url = 'http://127.0.0.1:8765/'
        stream.start.side_effect = OSError('模拟摄像头打开失败')
        with patch.object(camera_preview, 'CameraPreview', return_value=preview), \
                patch.object(camera_preview, 'CameraStream', return_value=stream), \
                patch('sys.stdout', new=io.StringIO()), self.assertLogs('camera_preview', level='ERROR'):
            self.assertEqual(camera_preview.main(['--no-local-browser']), 1)
        stream.close.assert_called_once()
        preview.stop.assert_called_once()

    def test_ssh_viewer_opens_computer_browser_after_verifying_service(self):
        import camera_view
        socket = Mock()
        socket.__enter__ = Mock(return_value=socket)
        socket.__exit__ = Mock(return_value=False)
        socket.connect_ex.return_value = 1
        process = Mock()
        process.poll.side_effect = [None, 0]
        process.wait.return_value = 0
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"service":"ningbo-camera-preview"}')
        with patch.object(camera_view.socket, 'socket', return_value=socket), \
                patch.object(camera_view.shutil, 'which', return_value='ssh'), \
                patch.object(camera_view.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(camera_view, 'build_opener', return_value=opener), \
                patch.object(camera_view.webbrowser, 'open', return_value=True) as browser, \
                patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(camera_view.main(['dianxin@192.0.2.1', '--local-port', '8766']), 0)
        browser.assert_called_once_with('http://127.0.0.1:8766/')
        self.assertIn('127.0.0.1:8766:127.0.0.1:8765', popen.call_args.args[0])
        self.assertEqual(popen.call_args.args[0][-1], 'dianxin@192.0.2.1')

    def test_ssh_start_failure_does_not_open_browser(self):
        import camera_view
        socket = Mock()
        socket.__enter__ = Mock(return_value=socket)
        socket.__exit__ = Mock(return_value=False)
        socket.connect_ex.return_value = 1
        process = Mock()
        process.poll.return_value = 1
        with patch.object(camera_view.socket, 'socket', return_value=socket), \
                patch.object(camera_view.shutil, 'which', return_value='ssh'), \
                patch.object(camera_view.subprocess, 'Popen', return_value=process), \
                patch.object(camera_view.webbrowser, 'open') as browser, patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(camera_view.main(['dianxin@192.0.2.1']), 1)
        browser.assert_not_called()


if __name__ == '__main__': unittest.main()
