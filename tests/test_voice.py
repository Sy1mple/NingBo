"""USB 语音生命周期、识别结果桥接和中文文本协议验证，不打开麦克风。"""
from dataclasses import replace
import queue
import threading
import time
import unittest
from unittest.mock import patch

from JETSON.Sensor import DEFAULT_CONFIG
from JETSON.Sensor.sensors.usb_microphone import UsbMicrophone
from JETSON.Voice.recognizer import VoskRecognizer
from JETSON.Voice.service import VoiceService
from Serial_Screen.voice_bridge import VoiceScreenBridge
from Serial_Screen.screen_service.screen import addresses
from Serial_Screen.screen_service.screen.message_parser import MessageParser
from Serial_Screen.screen_service.screen.screen_sender import ScreenSender
from Serial_Screen.screen_service.screen.service import ScreenService
from test_device_bridge import MemoryScreen


def wait_until(condition):
    deadline = time.monotonic() + 1
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("语音线程未在测试期限内完成")
        time.sleep(0.005)


class AudioFixture:
    device_name = "USB test input"
    def __init__(self, config):
        self.blocks = queue.Queue()
        self.started = False
        self.closed = False
    def prepare(self): return 48000
    def start(self): self.started = True
    def read(self, timeout):
        try: return self.blocks.get(timeout=timeout)
        except queue.Empty: return None
    def close(self): self.closed = True


class ServiceFixture:
    def __init__(self):
        self.session = 0
        self.stops = 0
        self.results = []
        self.error = ""
    def start(self):
        self.session += 1
        return {"accepted": True, "session_id": self.session}
    def stop(self):
        self.stops += 1
        self.results.clear()
        return {"accepted": True}
    def snapshot(self):
        return {"phase": "error" if self.error else "listening", "error": self.error}
    def poll_results(self):
        records, self.results = self.results, []
        return records


class VoiceTextTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG.voice, text_words=8)
        self.parser = MessageParser(voice_config=self.config)

    def test_chinese_frame_uses_registered_vp_and_byte_length(self):
        item = self.parser.parse({"scene": "voice", "recognized_text": "打开风扇"})[0]
        data = "打开风扇".encode("gbk") + b"\xff" * 8
        frame = ScreenSender.build_write_bytes_frame(item["address"], item["data"])
        self.assertEqual(frame, bytes.fromhex("5A A5 13 82 10 64") + data)
        self.assertEqual(frame[2], len(frame) - 3)

    def test_empty_and_shorter_text_clear_entire_reserved_buffer(self):
        empty = self.parser.parse({"scene": "voice", "recognized_text": ""})[0]
        short = self.parser.parse({"scene": "voice", "recognized_text": "开"})[0]
        self.assertEqual(empty["data"], b"\xff" * 16)
        self.assertEqual(short["data"], "开".encode("gbk") + b"\xff" * 14)

    def test_chinese_truncation_preserves_complete_characters_and_end_marker(self):
        item = self.parser.parse({"scene": "voice", "recognized_text": "一" * 20})[0]
        self.assertEqual(item["text"], "一" * 7)
        self.assertEqual(item["data"][:-2].decode("gbk"), "一" * 7)
        self.assertEqual(item["data"][-2:], b"\xff\xff")

    def test_ascii_alignment_and_unicode_big_endian(self):
        item = self.parser.parse({"scene": "voice", "recognized_text": "abc"})[0]
        self.assertTrue(item["data"].startswith(b"abc \xff\xff"))
        parser = MessageParser(voice_config=replace(self.config, text_encoding="utf-16-be"))
        item = parser.parse({"scene": "voice", "recognized_text": "你好"})[0]
        self.assertTrue(item["data"].startswith("你好".encode("utf-16-be") + b"\xff\xff"))

    def test_unsupported_text_and_buffer_overlap_are_rejected(self):
        for text in (123, "a\x00b", "你好\n", "😃"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.parser.parse({"scene": "voice", "recognized_text": text})
        with patch.dict(addresses.ADDRESS_MAP, {"another.value": 0x1065}):
            with self.assertRaisesRegex(ValueError, "冲突"):
                self.parser.parse({"scene": "voice", "recognized_text": "你好"})

    def test_text_sender_validates_frame_size_and_partial_serial_write(self):
        for address, data in ((0x1064, b"a"), (0x1064, b"a" * 254), (0xFFFF, b"a" * 4)):
            with self.subTest(address=address), self.assertRaises(ValueError):
                ScreenSender.build_write_bytes_frame(address, data)
        class Serial:
            is_open = True
            def write(self, frame): return len(frame) - 1
            def flush(self): pass
        sender = ScreenSender()
        sender.ser = Serial()
        with self.assertRaises(OSError):
            sender.send_updates(self.parser.parse({"scene": "voice", "recognized_text": "你好"}))


class VoiceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.screen = MemoryScreen()
        self.service = ServiceFixture()
        self.bridge = VoiceScreenBridge(self.screen, self.service)

    def test_default_idle_does_not_open_or_write_anything(self):
        self.assertEqual(self.bridge.tick(), [])
        self.assertEqual(self.service.session, 0)
        self.assertEqual(self.screen.frames, [])

    def test_start_result_text_stop_and_late_result(self):
        self.bridge.handle_event("voice.start")
        self.assertEqual(self.screen.frames[-1][4:6], b"\x10\x64")
        self.assertEqual(self.screen.frames[-1][6:], b"\xff" * 64)
        self.service.results = [{"session_id": 1, "text": "打开风扇", "final": True}]
        self.bridge.tick()
        self.assertTrue(self.screen.frames[-1][6:].startswith("打开风扇".encode("gbk")))
        self.bridge.handle_event("voice.stop")
        self.assertEqual(self.service.stops, 1)
        self.assertEqual(self.screen.frames[-1][6:], b"\xff" * 64)
        count = len(self.screen.frames)
        self.service.results = [{"session_id": 1, "text": "过期结果", "final": True}]
        self.bridge.tick()
        self.assertEqual(len(self.screen.frames), count)
        self.bridge.handle_event("voice.start")
        self.bridge.tick()
        self.assertEqual(self.screen.messages[-1]["recognized_text"], "")

    def test_duplicate_start_is_rejected_and_repeated_text_is_not_resent(self):
        self.bridge.handle_event("voice.start")
        self.assertFalse(self.bridge.handle_event("voice.start")["handled"])
        self.assertEqual(self.service.session, 1)
        self.service.results = [{"session_id": 1, "text": "你好", "final": False}]
        self.bridge.tick()
        count = len(self.screen.frames)
        self.service.results = [{"session_id": 1, "text": "你好", "final": True}]
        self.assertEqual(self.bridge.tick(), [])
        self.assertEqual(len(self.screen.frames), count)

    def test_failed_text_write_is_retried_without_losing_recognition(self):
        self.bridge.handle_event("voice.start")
        self.service.results = [{"session_id": 1, "text": "你好", "final": True}]
        with patch.object(self.screen, "handle_message", side_effect=OSError("serial failed")):
            with self.assertRaises(OSError): self.bridge.tick()
        self.assertTrue(self.bridge.tick())
        self.assertEqual(self.screen.messages[-1]["recognized_text"], "你好")

    def test_backend_failure_is_not_sent_as_recognized_text(self):
        self.bridge.handle_event("voice.start")
        self.service.error = "USB disconnected"
        with self.assertLogs("Serial_Screen.voice_bridge", level="ERROR"):
            self.assertEqual(self.bridge.tick(), [])
        self.assertFalse(self.bridge.active)
        self.assertEqual(self.screen.messages[-1]["recognized_text"], "")

    def test_unencodable_text_is_not_retried_and_next_valid_text_is_sent(self):
        self.bridge.handle_event('voice.start')
        self.service.results = [{'session_id': 1, 'text': '謝', 'final': True}]
        with self.assertLogs('Serial_Screen.voice_bridge', level='ERROR'):
            self.assertEqual(self.bridge.tick(), [])
        count = len(self.screen.messages)
        self.assertEqual(self.bridge.tick(), [])
        self.assertEqual(len(self.screen.messages), count)
        self.service.results = [{'session_id': 1, 'text': '谢谢', 'final': True}]
        self.assertTrue(self.bridge.tick())

    def test_real_screen_button_frames_reach_voice_service_and_text_frames(self):
        from application import NingBoApplication
        from test_application import DeviceFixture, ScreenFixture
        from Serial_Screen.screen_service.screen.screen_events import ScreenEventParser
        screen = ScreenFixture()
        parser = ScreenEventParser()
        app = NingBoApplication(devices=DeviceFixture(), screen=screen, voice_service=self.service)
        with app:
            screen.events = parser.feed(bytes.fromhex("5A A5 06 83 30 80 01 50 80"))
            app.process_once()
            self.assertEqual(self.service.session, 1)
            self.service.results = [{"session_id": 1, "text": "你好", "final": True}]
            app.process_once()
            self.assertEqual(screen.messages[-1], {"scene": "voice", "recognized_text": "你好"})
            screen.events = parser.feed(bytes.fromhex("5A A5 06 83 30 90 01 50 90"))
            app.process_once()
            self.assertEqual(screen.messages[-1], {"scene": "voice", "recognized_text": ""})
        self.assertGreaterEqual(self.service.stops, 1)


class RecognitionWorkerTests(unittest.TestCase):
    def make_service(self, **changes):
        config = replace(DEFAULT_CONFIG.voice, read_timeout=0.01, **changes)
        self.audio = AudioFixture(config)
        self.processed = []
        class Engine:
            def process(inner, pcm):
                self.processed.append(pcm)
                return {"text": "识别文字", "final": True}
        return VoiceService(config, microphone_factory=lambda cfg: self.audio,
                            recognizer_factory=lambda cfg, rate: Engine())

    def test_audio_to_results_and_stop_can_restart_with_new_session(self):
        service = self.make_service()
        result = service.start()
        try:
            wait_until(lambda: self.audio.started)
            self.assertFalse(service.start()["accepted"])
            self.audio.blocks.put(b"pcm-test-input")
            wait_until(lambda: service.snapshot()["text"] == "识别文字")
            results = service.poll_results()
            self.assertEqual(results[0]["text"], "识别文字")
            self.assertEqual(results[0]["session_id"], result["session_id"])
            self.assertEqual(self.processed, [b"pcm-test-input"])
        finally:
            service.stop()
        self.assertTrue(self.audio.closed)
        self.assertFalse(service.snapshot()["worker_alive"])
        self.audio = AudioFixture(service.config)
        second = service.start()
        try:
            self.assertNotEqual(second["session_id"], result["session_id"])
            wait_until(lambda: self.audio.started)
        finally:
            service.stop()

    def test_stop_during_model_loading_never_opens_capture(self):
        service = self.make_service()
        entered, release = threading.Event(), threading.Event()
        def load(cfg, rate):
            entered.set()
            release.wait(1)
            return object()
        service.recognizer_factory = load
        service.start()
        self.assertTrue(entered.wait(1))
        threading.Timer(0.03, release.set).start()
        service.stop()
        self.assertFalse(self.audio.started)
        self.assertTrue(self.audio.closed)
        self.assertEqual(service.poll_results(), [])

    def test_dependency_or_usb_failure_is_recorded_and_closed(self):
        service = self.make_service()
        self.audio.prepare = lambda: (_ for _ in ()).throw(OSError("USB missing"))
        service.start()
        wait_until(lambda: not service.snapshot()["worker_alive"])
        self.assertEqual(service.snapshot()["phase"], "error")
        self.assertIn("USB missing", service.snapshot()["error"])
        self.assertTrue(self.audio.closed)
        service.stop()

    def test_disabled_voice_does_not_create_audio(self):
        service = self.make_service(enabled=False)
        self.assertFalse(service.start()["accepted"])
        self.assertFalse(self.audio.started)

    def test_stop_timeout_blocks_overlapping_start_until_worker_exits(self):
        service = self.make_service()
        entered, release = threading.Event(), threading.Event()
        def load(cfg, rate):
            entered.set()
            release.wait(1)
            return object()
        service.recognizer_factory = load
        service.join_timeout = 0.01
        service.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaisesRegex(RuntimeError, "线程未结束"):
                service.stop()
            self.assertFalse(service.start()["accepted"])
        finally:
            release.set()
            wait_until(lambda: not service.snapshot()["worker_alive"])
            service.stop()
        self.assertFalse(self.audio.started)
        self.assertEqual(service.poll_results(), [])

    def test_vosk_adapter_parses_final_partial_and_rejects_bad_text(self):
        class Decoder:
            final = False
            def AcceptWaveform(self, data): return self.final
            def PartialResult(self): return '{"partial":"你好"}'
            def Result(self): return '{"text":"完整句子"}'
        decoder = Decoder()
        engine = VoskRecognizer(decoder)
        self.assertEqual(engine.process(b"pcm"), {"text": "你好", "final": False})
        decoder.final = True
        self.assertEqual(engine.process(b"pcm"), {"text": "完整句子", "final": True})
        decoder.Result = lambda: '{"text":123}'
        with self.assertRaises(ValueError): engine.process(b"pcm")


class MicrophoneTests(unittest.TestCase):
    def setUp(self):
        class Module:
            def __init__(inner):
                inner.devices = [{"name": "internal", "max_input_channels": 1, "default_samplerate": 48000},
                                 {"name": "AIMIC-M4 USB Audio", "max_input_channels": 1, "default_samplerate": 48000}]
            def query_devices(inner): return inner.devices
            def check_input_settings(inner, **kwargs): inner.settings = kwargs
            def RawInputStream(inner, **kwargs):
                inner.kwargs = kwargs
                class Stream:
                    active = False
                    def start(self): self.active = True
                    def abort(self): self.active = False
                    def close(self): self.closed = True
                inner.stream = Stream()
                return inner.stream
        self.module = Module()

    def test_usb_selection_native_rate_pcm_callback_and_release(self):
        audio = UsbMicrophone(DEFAULT_CONFIG.voice, module=self.module)
        self.assertEqual(audio.prepare(), 48000)
        audio.start()
        self.assertEqual(self.module.kwargs["device"], 1)
        self.assertEqual(self.module.kwargs["channels"], 1)
        self.assertEqual(self.module.kwargs["dtype"], "int16")
        self.module.kwargs["callback"](b"pcm", 1, None, False)
        self.assertEqual(audio.read(0.01), b"pcm")
        audio.close()
        self.assertTrue(self.module.stream.closed)

    def test_missing_and_ambiguous_usb_never_select_internal_microphone(self):
        self.module.devices = self.module.devices[:1]
        with self.assertRaisesRegex(RuntimeError, "0 个设备"):
            UsbMicrophone(DEFAULT_CONFIG.voice, module=self.module).prepare()
        self.module.devices = [dict(name="AIMIC-M4 USB A", max_input_channels=1, default_samplerate=16000),
                               dict(name="AIMIC-M4 USB B", max_input_channels=1, default_samplerate=16000)]
        with self.assertRaisesRegex(RuntimeError, "2 个设备"):
            UsbMicrophone(DEFAULT_CONFIG.voice, module=self.module).prepare()

    def test_linux_capture_node_and_hardware_address_select_same_aispeech_device(self):
        self.module.devices = [dict(name="internal (hw:0,0)", max_input_channels=1, default_samplerate=16000),
                               dict(name="AISPEECH USB Audio (hw:2,0)", max_input_channels=1, default_samplerate=48000),
                               dict(name="another device (hw:2,01)", max_input_channels=1, default_samplerate=16000)]
        for selected in ("hw:2,0", "/dev/snd/pcmC2D0c", "AISPEECH", 1):
            with self.subTest(selected=selected):
                audio = UsbMicrophone(replace(DEFAULT_CONFIG.voice, input_device=selected), module=self.module)
                audio.prepare()
                self.assertEqual(audio.device_index, 1)
        for invalid in ("/dev/ttyUSB0", "/dev/snd/pcmC2D0p"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                UsbMicrophone(replace(DEFAULT_CONFIG.voice, input_device=invalid), module=self.module).prepare()

    def test_queue_overflow_and_stream_disconnect_are_explicit_errors(self):
        audio = UsbMicrophone(replace(DEFAULT_CONFIG.voice, audio_queue_blocks=1), module=self.module)
        audio.start()
        callback = self.module.kwargs["callback"]
        callback(b"first", 1, None, False)
        callback(b"second", 1, None, False)
        with self.assertRaisesRegex(OSError, "队列已满"): audio.read(0.01)
        audio.close()
        audio = UsbMicrophone(DEFAULT_CONFIG.voice, module=self.module)
        audio.start()
        self.module.stream.active = False
        with self.assertRaisesRegex(OSError, "已断开"): audio.read(0.01)
        audio.close()

    def test_voice_config_limits(self):
        for values in ({"input_device": ""}, {"input_device": True}, {"text_words": 127},
                       {"sample_rate": 0}, {"text_encoding": "utf-8"}, {"read_timeout": 10}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, voice=replace(DEFAULT_CONFIG.voice, **values)).validate()

    def test_input_overflow_keeps_stream_and_delivers_current_block(self):
        from types import SimpleNamespace
        audio = UsbMicrophone(DEFAULT_CONFIG.voice, module=self.module)
        audio.start()
        self.assertEqual(self.module.kwargs['latency'], .5)
        with self.assertLogs('JETSON.Sensor.sensors.usb_microphone', level='WARNING'):
            self.module.kwargs['callback'](b'current', 1, None, SimpleNamespace(input_overflow=True))
        self.assertEqual(audio.read(.01), b'current')
        self.assertEqual(audio.input_overflows, 1)
        self.assertTrue(self.module.stream.active)
        audio.close()


if __name__ == "__main__":
    unittest.main()
