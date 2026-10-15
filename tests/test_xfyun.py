"""验证云端收发协议和配额，全部使用假的网络连接。"""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import base64
import numpy as np
from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Voice.xfyun_recognizer import DailyQuota, PcmEncoder, ResultAssembler, XfyunRecognizer, signed_url


class Timeout(Exception):
    pass


class FakeSocket:
    def __init__(self):
        self.incoming = queue.Queue()
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        pass

    def send(self, message):
        item = json.loads(message)
        self.sent.append(item)
        status = item['data']['status']
        result = {'sn': 0, 'pgs': 'apd', 'ws': [{'cw': [{'w': '你好'}]}]} if status == 0 else None
        if status in (0, 2):
            self.incoming.put(json.dumps({'code': 0, 'data': {'status': 2 if status == 2 else 1, 'result': result}}))

    def recv(self):
        try:
            return self.incoming.get(timeout=.05)
        except queue.Empty:
            raise Timeout()

    def close(self, timeout=None):
        self.closed = True
        self.incoming.put('')


class FakeNetwork:
    WebSocketTimeoutException = Timeout

    def __init__(self):
        self.socket = FakeSocket()
        self.calls = []

    def create_connection(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.socket


class XfyunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = replace(DEFAULT_CONFIG.voice, xfyun_credentials_file=str(Path(self.temp.name)/'missing'),
                           xfyun_usage_file=str(Path(self.temp.name)/'quota.sqlite3'), input_gain=1,
                           xfyun_trigger_seconds=.001, silence_seconds=.04)
        self.env = patch.dict('os.environ', {'XFYUN_APP_ID': 'test-app', 'XFYUN_API_KEY': 'test-key',
                                           'XFYUN_API_SECRET': 'test-secret'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_auth_url_uses_utc_date_hmac_and_official_host(self):
        date = datetime(2026,10,14,0,0,tzinfo=timezone.utc)
        params = parse_qs(urlsplit(signed_url(self.cfg.xfyun_endpoint, 'key', 'secret', date)).query)
        self.assertEqual(params['date'], ['Wed, 14 Oct 2026 00:00:00 GMT'])
        self.assertEqual(params['host'], ['iat-api.xfyun.cn'])
        auth = base64.b64decode(params['authorization'][0]).decode()
        self.assertIn('headers="host date request-line"', auth)
        self.assertNotIn('secret', auth)
        with self.assertRaises(ValueError):
            signed_url('wss://other.example/v2/iat', 'key', 'secret')

    def test_quota_persists_and_resets_by_day(self):
        now = [datetime(2026,10,14)]
        a = DailyQuota(self.cfg.xfyun_usage_file, 'app', 2, lambda: now[0])
        self.assertEqual(a.reserve(), 1)
        b = DailyQuota(self.cfg.xfyun_usage_file, 'app', 2, lambda: now[0])
        self.assertEqual(b.reserve(), 2)
        with self.assertRaisesRegex(RuntimeError, '上限'):
            a.reserve()
        now[0] = datetime(2026,10,15)
        self.assertEqual(a.reserve(), 1)

    def test_quota_concurrent_reservations_never_exceed_limit(self):
        lock = threading.Lock()
        accepted = []
        def reserve():
            try:
                value = DailyQuota(self.cfg.xfyun_usage_file, 'app', 2).reserve()
                with lock:
                    accepted.append(value)
            except RuntimeError:
                pass
        threads = [threading.Thread(target=reserve) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sorted(accepted), [1, 2])

    def test_resampler_preserves_state_across_irregular_blocks(self):
        raw = np.random.default_rng(0).integers(-12000,12000,2403,dtype=np.int16).tobytes()
        whole = PcmEncoder(48000, 1).convert(raw)[0]
        stream = PcmEncoder(48000, 1)
        chunks = [raw[:402],raw[402:1820],raw[1820:]]
        split = b''.join(stream.convert(c)[0] for c in chunks)
        np.testing.assert_array_equal(np.frombuffer(whole,dtype='<i2'), np.frombuffer(split,dtype='<i2'))

    def test_dynamic_result_replacement_is_not_appended_twice(self):
        a = ResultAssembler()
        def result(sn, text, **extra):
            return {'code':0,'data':{'status':1,'result':{'sn':sn,'ws':[{'cw':[{'w':text}]}],**extra}}}
        a.accept(result(1,'你好'))
        a.accept(result(2,'欢迎实验操'))
        self.assertEqual(a.accept(result(3,'欢迎实验箱',pgs='rpl',rg=[2,2]))['text'],'你好欢迎实验箱')
        self.assertTrue(a.accept({'code':0,'data':{'status':2}})['final'])

    def test_silence_uses_no_quota_and_speech_streams_until_final(self):
        network = FakeNetwork()
        r = XfyunRecognizer(self.cfg,16000,module=network)
        try:
            silence = np.zeros(640,dtype='<i2').tobytes()
            self.assertEqual(r.process(silence)['text'],'')
            self.assertEqual(network.calls,[])
            voice = np.full(640,3000,dtype='<i2').tobytes()
            r.process(voice)
            final = r.process(silence)
            self.assertEqual(final,{'text':'你好','final':True})
            messages = network.socket.sent
            self.assertEqual([m['data']['status'] for m in messages],[0,1,1,2])
            self.assertEqual(messages[0]['business']['rlang'],'zh-cn')
            self.assertEqual(messages[0]['business']['dwa'],'wpgs')
            self.assertNotIn('common',messages[1])
            self.assertEqual(len(base64.b64decode(messages[1]['data']['audio'])),1280)
        finally:
            r.close()
        self.assertTrue(network.socket.closed)
        self.assertIsNone(r.reader)

    def test_missing_credentials_and_exhausted_quota_do_not_open_network(self):
        network = FakeNetwork()
        with patch.dict('os.environ',{'XFYUN_APP_ID':'','XFYUN_API_KEY':'','XFYUN_API_SECRET':''}):
            with self.assertRaisesRegex(RuntimeError,'.env.voice'):
                XfyunRecognizer(self.cfg,16000,module=network)
        limit = DailyQuota(self.cfg.xfyun_usage_file,'test-app',1)
        limit.reserve()
        r = XfyunRecognizer(self.cfg,16000,module=network,quota=limit)
        try:
            with self.assertRaisesRegex(RuntimeError,'上限'):
                r.process(np.full(640,3000,dtype='<i2').tobytes())
            self.assertEqual(network.calls,[])
        finally:
            r.close()

    def test_server_error_is_reported_without_credential_values(self):
        a = ResultAssembler()
        with self.assertRaisesRegex(RuntimeError,'10010') as err:
            a.accept({'code':10010,'message':'echo-secret'})
        self.assertNotIn('echo-secret',str(err.exception))


if __name__ == '__main__':
    unittest.main()
