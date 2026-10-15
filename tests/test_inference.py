"""验证硬件后端选择、CUDA 缺失拒绝、图像后处理与语音分句契约。"""
from dataclasses import replace
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Vision.engine import FaceEngine
from JETSON.Vision.tensorrt_models import decode_yunet, align_face
from JETSON.Inference.tensorrt_runner import TensorRTRunner
from JETSON.Voice.whisper_recognizer import WhisperRecognizer, WhisperRecognizerFactory


class ImageTests(unittest.TestCase):
    def outputs(self):
        data = {}
        for stride in (8, 16, 32):
            count = (320 // stride)**2
            for name, size in (("cls", 1), ("obj", 1), ("bbox", 4), ("kps", 10)):
                data[f"{name}_{stride}"] = np.zeros((1, count, size), dtype=np.float32)
        return data

    def test_yunet_score_geometry_and_no_face(self):
        outputs = self.outputs()
        self.assertIsNone(decode_yunet(outputs, 320, 320, DEFAULT_CONFIG.face, cv2))
        index = 10 * 40 + 10
        outputs["cls_8"][0, index, 0] = 1
        outputs["obj_8"][0, index, 0] = 1
        outputs["bbox_8"][0, index] = [0, 0, np.log(10), np.log(10)]
        faces = decode_yunet(outputs, 320, 320, DEFAULT_CONFIG.face, cv2)
        self.assertEqual(faces.shape, (1, 15))
        np.testing.assert_allclose(faces[0, :4], [40, 40, 80, 80], atol=1e-4)
        np.testing.assert_allclose(faces[0, 4:14], [80] * 10)
        outputs["obj_8"][0, index, 0] = .2
        self.assertIsNone(decode_yunet(outputs, 320, 320, DEFAULT_CONFIG.face, cv2))

    def test_face_alignment_matches_known_translation(self):
        rng = np.random.default_rng(42)
        image = rng.integers(0, 256, (180, 180, 3), dtype=np.uint8)
        target = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                           [41.5493, 92.3655], [70.7299, 92.2041]])
        face = np.r_[0, 0, 100, 100, (target + [20, 30]).reshape(-1), 1]
        aligned = align_face(image, face, cv2)
        np.testing.assert_array_equal(aligned, image[30:142, 20:132])
        face[4:14] = 0
        with self.assertRaises(ValueError):
            align_face(image, face, cv2)

    def test_default_face_backend_does_not_call_cpu_on_gpu_failure(self):
        self.assertEqual(DEFAULT_CONFIG.face.backend, "tensorrt")
        engine = FaceEngine(DEFAULT_CONFIG.face)
        with patch("JETSON.Vision.tensorrt_models.TensorRTDetector", side_effect=RuntimeError("no GPU")), \
             patch.object(cv2.FaceDetectorYN, "create") as cpu, self.assertRaisesRegex(RuntimeError, "no GPU"):
            engine.load()
        cpu.assert_not_called()

    def test_explicit_cpu_debug_does_not_change_default_config(self):
        engine = FaceEngine(replace(DEFAULT_CONFIG.face, backend="opencv_cpu"))
        try:
            result = engine.analyze(np.zeros((480, 640, 3), dtype=np.uint8))
            self.assertFalse(result["valid"])
        finally:
            engine.close()
        self.assertEqual(DEFAULT_CONFIG.face.backend, "tensorrt")


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG.voice, minimum_utterance_seconds=.2, silence_seconds=.2,
                              maximum_utterance_seconds=.5)
        self.model = Mock()
        self.model.transcribe.return_value = {"text": "打开风扇", "segments": [{"no_speech_prob": .1}]}

    def pcm(self, value, duration=.1, rate=16000):
        return np.full(int(rate * duration), value, dtype="<i2").tobytes()

    def test_silence_never_infers_speech_and_short_noise_rejected(self):
        recognizer = WhisperRecognizer(self.model, self.config, 16000)
        for _ in range(10):
            self.assertEqual(recognizer.process(self.pcm(0))["text"], "")
        recognizer.process(self.pcm(3000))
        recognizer.process(self.pcm(0))
        self.assertEqual(recognizer.process(self.pcm(0)), {"text": "", "final": True})
        self.model.transcribe.assert_not_called()

    def test_phrase_finishes_on_silence_and_uses_fp16_chinese(self):
        recognizer = WhisperRecognizer(self.model, self.config, 16000)
        recognizer.process(self.pcm(3000))
        recognizer.process(self.pcm(3000))
        recognizer.process(self.pcm(0))
        result = recognizer.process(self.pcm(0))
        self.assertEqual(result, {"text": "打开风扇", "final": True})
        arguments = self.model.transcribe.call_args.kwargs
        self.assertTrue(arguments["fp16"])
        self.assertEqual(arguments["language"], "zh")
        self.assertFalse(arguments["condition_on_previous_text"])

    def test_max_duration_flush_and_model_noise_suppression(self):
        recognizer = WhisperRecognizer(self.model, self.config, 16000)
        self.model.transcribe.return_value = {"text": "noise hallucination", "segments": [{"no_speech_prob": .9}]}
        for _ in range(4):
            self.assertFalse(recognizer.process(self.pcm(3000))["final"])
        self.assertEqual(recognizer.process(self.pcm(3000)), {"text": "", "final": True})

    def test_48k_capture_resampled_to_16k_before_inference(self):
        recognizer = WhisperRecognizer(self.model, self.config, 48000)
        recognizer.process(self.pcm(3000, .2, 48000))
        result = recognizer.process(self.pcm(0, .2, 48000))
        self.assertTrue(result["final"])
        waveform = self.model.transcribe.call_args.args[0]
        self.assertEqual(waveform.shape, (6400,))
        self.assertEqual(waveform.dtype, np.float32)

    def test_missing_cuda_rejected_before_cpu_model_loading(self):
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        whisper = Mock()
        with patch.dict(sys.modules, {"torch": torch, "whisper": whisper}), self.assertRaisesRegex(RuntimeError, "CUDA"):
            WhisperRecognizerFactory()(self.config, 16000)
        whisper.load_model.assert_not_called()

    def test_gain_allows_quiet_microphone_phrase_to_reach_recognition(self):
        recognizer = WhisperRecognizer(self.model, self.config, 16000)
        recognizer.process(self.pcm(40))
        recognizer.process(self.pcm(40))
        recognizer.process(self.pcm(0))
        result = recognizer.process(self.pcm(0))
        self.assertEqual(result['text'], '打开风扇')

    def test_nonfinite_recognition_is_rejected(self):
        self.model.transcribe.return_value = {'text': '!!!!', 'segments': [{'no_speech_prob': float('nan')}]}
        recognizer = WhisperRecognizer(self.model, self.config, 16000)
        recognizer.process(self.pcm(3000, .2))
        with self.assertRaisesRegex(RuntimeError, '非有限'):
            recognizer.process(self.pcm(0, .2))


class RunnerTests(unittest.TestCase):
    def test_execute_failure_never_returns_stale_outputs(self):
        runner = TensorRTRunner.__new__(TensorRTRunner)
        runner.np = np
        runner.cuda = Mock()
        runner.context = Mock()
        runner.context.execute_v2.return_value = False
        runner._buffers = [{"input": True, "name": "input", "array": np.zeros((1, 3, 2, 2), dtype=np.float32), "pointer": 1},
                           {"input": False, "name": "output", "array": np.ones((1, 128), dtype=np.float32), "pointer": 2}]
        with self.assertRaises(RuntimeError):
            runner.infer(np.zeros((1, 3, 2, 2), dtype=np.float32))
        runner.cuda.copy_from_device.assert_not_called()

    def test_invalid_accelerator_configuration(self):
        for values in ({"backend": "auto"}, {"dla_core": 2}, {"dla_core": True},
                       {"dla_core": 0, "precision": "fp32"}, {"cuda_device": -1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, face=replace(DEFAULT_CONFIG.face, **values)).validate()
        for values in ({"backend": "auto"}, {"cuda_device": True}, {"maximum_utterance_seconds": 30}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_CONFIG, voice=replace(DEFAULT_CONFIG.voice, **values)).validate()


if __name__ == "__main__":
    unittest.main()
