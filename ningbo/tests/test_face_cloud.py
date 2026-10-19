"""离线测试：模拟讯飞响应及摄像头；不上传照片、不连接设备。"""
import base64
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from config import DEFAULT_CONFIG
from experiments.access.jetson.cloud_library import CloudFaceLibrary
from experiments.access.jetson.cloud_service import capture, match
from experiments.access.jetson.service import FaceService
from experiments.access.jetson.xfyun_client import XfyunFaceClient, TrialQuota, signed_url


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = replace(DEFAULT_CONFIG.face, database_path=str(self.root / 'faces.db'),
                              usage_file=str(self.root / 'usage.db'), credentials_file=str(self.root / 'keys'))
        self.keys = {'XFYUN_FACE_APP_ID': 'test-app', 'XFYUN_FACE_API_KEY': 'test-key',
                     'XFYUN_FACE_API_SECRET': 'test-secret', 'XFYUN_FACE_DETECT_LIMIT': '2',
                     'XFYUN_FACE_COMPARE_LIMIT': '2', 'XFYUN_FACE_FREE_EXPIRES': '2099-01-01T00:00:00+08:00'}
        self.root.joinpath('keys').write_text('\n'.join(k+'='+v for k,v in self.keys.items()), encoding='utf-8')
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def client(self, reply):
        class Opener:
            def open(inner, request, timeout):
                inner.request = request
                return io.BytesIO(json.dumps(reply).encode())
        with patch.dict('os.environ', self.keys):
            return XfyunFaceClient(self.config, opener=Opener())

    def response(self, result, code=0):
        return {'header': {'code': code}, 'payload': {'face_compare_result': {
                'text': base64.b64encode(json.dumps(result).encode()).decode()}}}

    def test_post_signature(self):
        now = datetime(2026, 10, 16, tzinfo=timezone.utc)
        query = parse_qs(urlsplit(signed_url('key', 'secret', now)).query)
        auth = base64.b64decode(query['authorization'][0]).decode()
        source = 'host: api.xf-yun.com\ndate: Fri, 16 Oct 2026 00:00:00 GMT\nPOST /v1/private/s67c9c78c HTTP/1.1'
        expected = base64.b64encode(hmac.new(b'secret', source.encode(), hashlib.sha256).digest()).decode()
        self.assertIn('signature="' + expected + '"', auth)

    def test_success_and_wire_payload(self):
        client = self.client(self.response({'ret': 0, 'score': .93}))
        self.assertEqual(client.compare(b'photo-one', b'photo-two', self.cancel), .93)
        body = json.loads(client.opener.request.data)
        self.assertEqual(body['parameter']['s67c9c78c']['service_kind'], 'face_compare')
        self.assertEqual(base64.b64decode(body['payload']['input2']['image']), b'photo-two')

    def test_bad_responses_never_succeed(self):
        replies = [self.response({'ret': 1, 'score': .99}), self.response({'ret': 0, 'score': .99}, 11200),
                   self.response({'ret': 0}), self.response({'ret': 0, 'score': float('nan')}),
                   self.response({'ret': 0, 'score': True}), self.response({'ret': 0, 'score': 100}), {}]
        for reply in replies:
            with self.subTest(reply=reply), patch.object(TrialQuota, 'reserve'):
                with self.assertRaises(RuntimeError):
                    self.client(reply).compare(b'a', b'b', self.cancel)

    def test_trial_survives_restart_and_separates_capabilities(self):
        TrialQuota(self.config.usage_file, self.keys).reserve('face_compare')
        TrialQuota(self.config.usage_file, self.keys).reserve('face_compare')
        with self.assertRaises(RuntimeError):
            TrialQuota(self.config.usage_file, self.keys).reserve('face_compare')
        TrialQuota(self.config.usage_file, self.keys).reserve('face_detect')

    def test_expired_trial_creates_no_usage(self):
        with self.assertRaises(RuntimeError):
            TrialQuota(self.config.usage_file, self.keys,
                       clock=lambda: datetime(2100, 1, 1, tzinfo=timezone.utc)).reserve('face_compare')
        self.assertFalse(Path(self.config.usage_file).exists())

    def test_console_dash_retains_total_quota(self):
        keys = {**self.keys, 'XFYUN_FACE_FREE_EXPIRES': '-'}
        TrialQuota(self.config.usage_file, keys).reserve('face_compare')
        TrialQuota(self.config.usage_file, keys).reserve('face_compare')
        with self.assertRaises(RuntimeError):
            TrialQuota(self.config.usage_file, keys).reserve('face_compare')
        self.assertIsNone(TrialQuota(self.config.usage_file, keys).expiry)

    def test_empty_or_malformed_expiry_is_rejected(self):
        from experiments.access.jetson.xfyun_client import trial_expiry
        for value in ('', 'unknown', '2026-11-15', 'forever'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                trial_expiry(value)

    def test_cancel_before_request_creates_no_usage(self):
        client = self.client(self.response({'ret': 0, 'score': .93}))
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            client.compare(b'a', b'b', self.cancel)
        self.assertFalse(Path(self.config.usage_file).exists())

    def test_photo_library_and_rename(self):
        library = CloudFaceLibrary(self.config.database_path)
        library.initialize()
        person = library.add_photo(b'jpeg', '张三')
        library.rename(person['person_id'], '李四')
        self.assertEqual(library.samples()[0]['name'], '李四')
        self.assertEqual(library.list_people()[0]['samples'], 1)
        self.assertEqual(library.samples()[0]['jpeg'], b'jpeg')
        from experiments.access.jetson.library import FaceLibrary
        with self.assertRaises(ValueError):
            FaceLibrary(self.config.database_path).initialize('old-model')

    def test_ambiguous_people_and_low_score_rejected(self):
        gallery = [{'person_id': 1, 'name': '甲', 'jpeg': b'a'}, {'person_id': 2, 'name': '乙', 'jpeg': b'b'}]
        class Client:
            def compare(inner, current, photo, cancel):
                return .95 if photo == b'a' else .94
        self.assertFalse(match(Client(), b'c', gallery, self.config, self.cancel)['recognized'])
        self.assertFalse(match(Client(), b'c', gallery, replace(self.config, cloud_threshold=.99), self.cancel)['recognized'])

    def test_multi_face_image_not_compared(self):
        import numpy as np
        class Camera:
            def read(inner):
                return np.zeros((480, 640, 3), dtype=np.uint8)
        class Client:
            def detect(inner, jpeg, cancel):
                return {'face_num': 2}
        service = FaceService(self.config, DEFAULT_CONFIG.camera)
        image, note, count = capture(service, Client(), Camera(), self.cancel)
        self.assertIsNone(image)
        self.assertEqual(count, 2)

    def test_enrollment_and_duplicate_use_existing_screen_protocol(self):
        class Camera:
            def start(inner): pass
            def close(inner): pass
        class Client:
            def __init__(inner, config): pass
            def compare(inner, first, second, cancel): return .99
        service = FaceService(self.config, DEFAULT_CONFIG.camera, camera_factory=lambda cfg: Camera(), cloud_client_factory=Client)
        with patch('experiments.access.jetson.cloud_service.capture', return_value=(b'photo', '有效人脸', 1)):
            self.assertTrue(service.enroll()['accepted'])
            service._worker.join(2)
            records = service.poll_results()
            self.assertEqual(records[0]['event'], 'enrollment')
            self.assertTrue(records[0]['success'])
            self.assertTrue(service.enroll()['accepted'])
            service._worker.join(2)
            self.assertTrue(service.poll_results()[0]['person']['duplicate'])
        self.assertEqual(len(service.library.list_people()), 1)

    def test_stop_during_network_prevents_enrollment_commit(self):
        entered = threading.Event()
        class Camera:
            def start(inner): pass
            def close(inner): pass
        service = FaceService(self.config, DEFAULT_CONFIG.camera, camera_factory=lambda cfg: Camera(),
                              cloud_client_factory=lambda cfg: object())
        def delayed_capture(s, client, camera, cancel):
            entered.set()
            cancel.wait(2)
            return b'photo', '有效人脸', 1
        with patch('experiments.access.jetson.cloud_service.capture', side_effect=delayed_capture):
            service.enroll()
            self.assertTrue(entered.wait(2))
            service.stop()
        self.assertEqual(service.library.list_people(), [])
        self.assertEqual(service.poll_results(), [])

    def test_recognition_requires_two_rounds_and_preserves_session(self):
        ready = threading.Event()
        class Camera:
            def start(inner): pass
            def close(inner): pass
        class Client:
            def __init__(inner, cfg): pass
            def compare(inner, first, second, cancel): return .99
        service = FaceService(replace(self.config, cloud_round_interval=1), DEFAULT_CONFIG.camera,
                              camera_factory=lambda cfg: Camera(), cloud_client_factory=Client)
        service.library.initialize()
        person = service.library.add_photo(b'known', '测试人员')
        records = []
        original = service._emit
        def emit(generation, event, **values):
            original(generation, event, **values)
            records.append(values)
            if values.get('recognized') is True:
                ready.set()
        with patch('experiments.access.jetson.cloud_service.capture', return_value=(b'current', '有效人脸', 1)), \
                patch.object(service, '_emit', side_effect=emit):
            service.start(42)
            self.assertTrue(ready.wait(3))
            service.stop()
        successes = [r for r in records if r.get('recognized') is True]
        self.assertEqual(len(successes), 1)
        self.assertEqual(successes[0]['session_id'], 42)
        self.assertEqual(successes[0]['person_id'], person['person_id'])
        self.assertTrue(any(r.get('note') == '正在连续确认人员' and r['recognized'] is None for r in records))

    def test_api_failure_surfaces_as_task_error_without_success(self):
        class Camera:
            def start(inner): pass
            def close(inner): pass
        service = FaceService(self.config, DEFAULT_CONFIG.camera, camera_factory=lambda cfg: Camera(),
                              cloud_client_factory=lambda cfg: object())
        service.library.initialize()
        service.library.add_photo(b'known', '测试人员')
        with patch('experiments.access.jetson.cloud_service.capture', side_effect=RuntimeError('免费额度不足')):
            service.start(7)
            service._worker.join(2)
        self.assertEqual(service.snapshot()['phase'], 'error')
        self.assertIn('免费额度不足', service.snapshot()['error'])
        self.assertFalse(any(r.get('recognized') is True for r in service.poll_results()))
        service.stop()


if __name__ == '__main__':
    unittest.main()
