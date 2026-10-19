"""真实线程和模拟 WebSocket 验证连续聆听；不采音、不调用云端。"""
from dataclasses import replace
import json
import queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from experiments.voice.bridge import VoiceScreenBridge
from experiments.voice.jetson.microphone import UsbMicrophone, AudioBacklogError
from experiments.voice.jetson.service import VoiceService
from experiments.voice.jetson.xfyun_recognizer import XfyunRecognizer, XfyunServiceError

VOICE = (300).to_bytes(2, 'little', signed=True) * 640
SILENCE = b'\x00\x00' * 640


def final(text='你好'):
    return json.dumps({'code': 0, 'data': {'status': 2, 'result': {
        'sn': 0, 'ws': [{'cw': [{'w': text}]}]}}})


class Timeout(Exception):
    pass


class Socket:
    def __init__(self, action=None):
        self.action = action
        self.messages = []
        self.responses = queue.Queue()
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def send(self, data):
        if self.closed:
            raise OSError('closed')
        message = json.loads(data)
        self.messages.append(message)
        if self.action:
            self.action(self, message)
        elif message['data']['status'] == 2:
            self.responses.put(final())

    def recv(self):
        try:
            return self.responses.get(timeout=.02)
        except queue.Empty:
            raise Timeout()

    def close(self, timeout=None):
        self.closed = True
        self.responses.put('')


class RecognizerTests(unittest.TestCase):
    def create(self, action=None, factory=None, quota=None, **options):
        self.sockets = []
        def connect(*args, **kwargs):
            sock = Socket(action)
            self.sockets.append(sock)
            return sock
        self.module = SimpleNamespace(WebSocketTimeoutException=Timeout,
                                      create_connection=Mock(side_effect=factory or connect))
        self.quota = quota or Mock(reserve=Mock(return_value=1))
        cfg = replace(DEFAULT_CONFIG.voice, xfyun_trigger_seconds=.04, silence_seconds=.04, **options)
        with patch('experiments.voice.jetson.xfyun_recognizer.credentials', return_value={
            'XFYUN_APP_ID': 'offline', 'XFYUN_API_KEY': 'offline', 'XFYUN_API_SECRET': 'offline'}):
            self.engine = XfyunRecognizer(cfg, 16000, module=self.module, quota=self.quota)
        self.addCleanup(self.engine.close)
        return self.engine

    def wait(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.005)
        self.fail('等待模拟语音状态超时')

    def test_two_sentences_with_pause_keep_listening_and_deliver_both_finals(self):
        engine = self.create()
        for n in (1, 2):
            engine.process(VOICE)
            self.wait(lambda: len(self.sockets) == n and self.sockets[-1].messages)
            engine.process(SILENCE)
            self.wait(lambda: engine.ws is None and bool(engine.results))
            self.assertEqual(engine.process(b''), {'text': '你好', 'final': True})
            self.assertTrue(engine.worker.is_alive())
            self.assertIsNone(engine._fatal)
        self.assertEqual(self.quota.reserve.call_count, 2)
        self.assertTrue(all(sock.closed for sock in self.sockets))

    def test_final_during_send_pacing_does_not_send_to_ended_session(self):
        engine = self.create()
        engine.process(VOICE)
        self.wait(lambda: self.sockets and len(self.sockets[0].messages) == 1)
        engine.next_send = time.monotonic() + .3
        engine.process(VOICE)
        self.sockets[0].responses.put(final('第一句'))
        self.wait(lambda: engine.ws is None)
        self.assertEqual(len(self.sockets[0].messages), 1)
        self.assertIsNone(engine._fatal)
        self.assertEqual(engine.process(b'')['text'], '第一句')
        engine.process(VOICE)
        self.wait(lambda: len(self.sockets) == 2)

    def test_final_racing_with_send_exception_is_normal_completion(self):
        def ending(sock, message):
            sock.responses.put(final())
            raise OSError('server closed normally')
        engine = self.create(action=ending)
        engine.process(VOICE)
        self.wait(lambda: self.sockets and engine.ws is None and bool(engine.results))
        self.assertIsNone(engine._fatal)
        self.assertEqual(engine.process(b'')['final'], True)

    def test_slow_connect_does_not_block_audio_delivery(self):
        entered, release = threading.Event(), threading.Event()
        def connect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return Socket()
        engine = self.create(factory=connect)
        self.addCleanup(release.set)
        engine.process(VOICE)
        self.assertTrue(entered.wait(1))
        for _ in range(60):
            engine.process(VOICE)
        self.assertFalse(release.is_set())
        self.assertEqual(engine._audio.qsize(), 60)
        self.assertIsNone(engine._fatal)
        release.set()

    def test_network_audio_backlog_is_bounded_and_recovers(self):
        entered, release = threading.Event(), threading.Event()
        def connect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return Socket()
        engine = self.create(factory=connect, audio_queue_blocks=3)
        self.addCleanup(release.set)
        engine.process(VOICE)
        self.assertTrue(entered.wait(1))
        for _ in range(8):
            engine.process(VOICE)
        self.assertLessEqual(engine._audio.qsize(), 3)
        self.assertGreater(engine.dropped_audio_blocks, 0)
        release.set()
        self.wait(lambda: not engine._reset_audio.is_set())
        self.assertTrue(engine.worker.is_alive())
        self.assertIsNone(engine._fatal)

    def test_transport_reconnects_but_has_a_retry_limit(self):
        def failed(sock, message):
            raise OSError('do not expose underlying secret URL')
        engine = self.create(action=failed)
        for n in range(1, 5):
            engine._retry_at = 0
            engine.process(VOICE)
            self.wait(lambda: engine._failures >= n)
            if n < 4:
                self.wait(lambda: engine.snapshot()['phase'] == 'recovering')
                self.assertTrue(engine.worker.is_alive())
        self.wait(lambda: not engine.worker.is_alive())
        self.assertEqual(self.quota.reserve.call_count, 4)
        with self.assertRaisesRegex(RuntimeError, '连接连续失败'):
            engine.process(VOICE)
        self.assertNotIn('secret', str(engine._fatal))

    def test_api_authorization_error_is_not_retried(self):
        def denied(sock, message):
            sock.responses.put(json.dumps({'code': 11200}))
        engine = self.create(action=denied)
        engine.process(VOICE)
        self.wait(lambda: not engine.worker.is_alive())
        self.assertEqual(self.quota.reserve.call_count, 1)
        with self.assertRaisesRegex(XfyunServiceError, '11200'):
            engine.process(VOICE)

    def test_transient_failure_recovers_and_next_sentence_succeeds(self):
        attempts = []
        def connect(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('temporary outage')
            return Socket()
        engine = self.create(factory=connect)
        engine.process(VOICE)
        self.wait(lambda: engine.snapshot()['phase'] == 'recovering')
        engine._retry_at = 0
        engine.process(VOICE)
        self.wait(lambda: engine.ws is not None and engine.ws.messages)
        engine.process(SILENCE)
        self.wait(lambda: engine.ws is None and bool(engine.results))
        self.assertEqual(engine.process(b''), {'text': '你好', 'final': True})
        self.assertEqual(engine._failures, 0)
        self.assertTrue(engine.worker.is_alive())

    def test_stop_during_connect_does_not_open_a_late_session(self):
        entered, release = threading.Event(), threading.Event()
        sock = Socket()
        def connect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return sock
        engine = self.create(factory=connect)
        self.addCleanup(release.set)
        engine.process(VOICE)
        self.assertTrue(entered.wait(1))
        close = threading.Thread(target=engine.close)
        close.start()
        self.wait(lambda: engine._stop.is_set())
        release.set()
        close.join(2)
        self.assertFalse(close.is_alive())
        self.assertFalse(engine.worker.is_alive())
        self.assertTrue(sock.closed)
        self.assertEqual(sock.messages, [])

    def test_daily_quota_error_does_not_open_or_retry_connections(self):
        quota = Mock(reserve=Mock(side_effect=RuntimeError('今日上限')))
        engine = self.create(quota=quota)
        engine.process(VOICE)
        self.wait(lambda: not engine.worker.is_alive())
        self.module.create_connection.assert_not_called()
        self.assertEqual(quota.reserve.call_count, 1)
        with self.assertRaisesRegex(RuntimeError, '今日上限'):
            engine.process(VOICE)

    def test_stop_interrupts_sender_and_joins_both_threads(self):
        engine = self.create()
        engine.process(VOICE)
        self.wait(lambda: self.sockets and self.sockets[0].messages)
        reader = engine.reader
        engine.next_send = time.monotonic() + 10
        engine.process(VOICE)
        engine.close()
        self.assertFalse(engine.worker.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertTrue(self.sockets[0].closed)


class CaptureAndScreenTests(unittest.TestCase):
    def test_microphone_overflow_can_resume_on_fresh_audio(self):
        mic = UsbMicrophone(replace(DEFAULT_CONFIG.voice, audio_queue_blocks=2), module=Mock())
        for _ in range(3):
            mic._callback(VOICE, 640, None, None)
        with self.assertRaises(AudioBacklogError):
            mic.read(.01)
        mic._callback(VOICE, 640, None, None)
        self.assertEqual(mic.read(.01), VOICE)
        self.assertFalse(mic._closed.is_set())

    def test_service_resets_utterance_after_audio_backlog_and_keeps_listening(self):
        cancel = threading.Event()
        audio = Mock(device_name='fake mic')
        audio.prepare.return_value = 16000
        audio.read.side_effect = [AudioBacklogError('backlog'), VOICE]
        engine = Mock()
        def recognized(pcm):
            cancel.set()
            return {'text': '你好', 'final': True}
        engine.process.side_effect = recognized
        engine.snapshot.return_value = {'phase': 'listening', 'notice': '正在聆听'}
        service = VoiceService(DEFAULT_CONFIG.voice, microphone_factory=lambda cfg: audio,
                               recognizer_factory=lambda cfg, rate: engine)
        service._run(0, cancel)
        engine.reset_audio.assert_called_once()
        engine.process.assert_called_once_with(VOICE)
        self.assertEqual(service.snapshot()['error'], '')
        audio.close.assert_called_once()

    def test_screen_shows_recovery_and_error_and_retries_failed_display_write(self):
        screen, service = Mock(), Mock()
        screen.handle_message.return_value = []
        service.start.return_value = {'accepted': True, 'session_id': 1}
        service.poll_results.return_value = []
        bridge = VoiceScreenBridge(screen, service=service)
        bridge.handle_event('voice.start')
        service.snapshot.return_value = {'phase': 'listening', 'engine_phase': 'recovering',
                                         'notice': '语音连接中断，正在恢复'}
        bridge.tick()
        self.assertEqual(screen.handle_message.call_args.args[0]['recognized_text'], '语音连接中断，正在恢复')
        service.snapshot.return_value = {'phase': 'error', 'error': '今日上限'}
        screen.handle_message.side_effect = [OSError('serial busy'), []]
        with self.assertRaises(OSError):
            bridge.tick()
        bridge.tick()
        self.assertFalse(bridge.active)
        self.assertEqual(screen.handle_message.call_args.args[0]['recognized_text'], '语音已停止：今日上限')

    def test_normal_waiting_notice_preserves_the_last_recognized_text(self):
        screen, service = Mock(), Mock()
        screen.handle_message.return_value = []
        service.start.return_value = {'accepted': True, 'session_id': 1}
        service.snapshot.return_value = {'phase': 'listening', 'engine_phase': 'listening',
                                         'notice': '正在聆听，请继续说话'}
        service.poll_results.side_effect = [[{'session_id': 1, 'text': '你好', 'final': True}], []]
        bridge = VoiceScreenBridge(screen, service=service)
        bridge.handle_event('voice.start')
        bridge.tick()
        count = screen.handle_message.call_count
        bridge.tick()
        self.assertEqual(screen.handle_message.call_count, count)
        self.assertEqual(bridge._last_text, '你好')

    def test_screen_returns_to_listening_after_recovery_and_next_start_is_allowed(self):
        screen, service = Mock(), Mock()
        screen.handle_message.return_value = []
        service.start.return_value = {'accepted': True, 'session_id': 1}
        service.poll_results.return_value = []
        bridge = VoiceScreenBridge(screen, service=service)
        bridge.handle_event('voice.start')
        service.snapshot.return_value = {'phase': 'listening', 'engine_phase': 'recovering',
                                         'notice': '语音连接中断，正在恢复'}
        bridge.tick()
        service.snapshot.return_value = {'phase': 'listening', 'engine_phase': 'listening',
                                         'notice': '正在聆听，请继续说话'}
        bridge.tick()
        self.assertEqual(bridge._last_text, '正在聆听，请继续说话')
        service.snapshot.return_value = {'phase': 'error', 'error': '连接连续失败'}
        bridge.tick()
        self.assertTrue(bridge.handle_event('voice.start')['handled'])
