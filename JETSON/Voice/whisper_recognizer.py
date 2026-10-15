"""CUDA Whisper 语音推理；PCM 分句在 CPU 上完成，模型仅在 GPU 上执行。"""
from pathlib import Path
import math


class WhisperRecognizerFactory:
    def __init__(self):
        self._model = None
        self._key = None

    def __call__(self, config, sample_rate):
        import torch
        if not torch.cuda.is_available() or config.cuda_device >= torch.cuda.device_count():
            raise RuntimeError("Whisper 需要可用的 CUDA GPU；请安装匹配 JetPack 的 NVIDIA PyTorch，不会转 CPU")
        import whisper
        path = Path(config.model_path).resolve()
        if not path.is_file() or path.suffix != ".pt":
            raise FileNotFoundError(f"Whisper 本地模型文件无效: {path}；使用 --voice-model 指定 .pt 文件")
        device = f"cuda:{config.cuda_device}"
        key = (str(path), path.stat().st_mtime_ns, device)
        if self._model is None or key != self._key:
            model = whisper.load_model(str(path), device=device)
            if str(model.device) != device:
                raise RuntimeError("Whisper 模型未加载到指定 CUDA GPU")
            self._model, self._key = model, key
        return WhisperRecognizer(self._model, config, sample_rate, audio_device=device)


class WhisperRecognizer:
    def __init__(self, model, config, sample_rate, audio_device=None):
        self.model, self.config, self.sample_rate = model, config, sample_rate
        self.audio_device = audio_device
        self._chunks = []
        self._samples = 0
        self._silent = 0
        self._speaking = False

    def process(self, pcm):
        import numpy as np
        if len(pcm) % 2:
            raise ValueError("麦克风 PCM 必须是完整的 int16 样本")
        block = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        block = np.clip(block * self.config.input_gain, -1.0, 1.0)
        if block.size == 0:
            return {"text": "", "final": False}
        voiced = float(np.sqrt(np.mean(block * block))) >= self.config.silence_rms
        if not self._speaking:
            if not voiced:
                return {"text": "", "final": False}
            self._speaking = True
        self._chunks.append(block)
        self._samples += len(block)
        self._silent = 0 if voiced else self._silent + len(block)
        ready = (self._silent >= self.config.silence_seconds * self.sample_rate or
                 self._samples >= self.config.maximum_utterance_seconds * self.sample_rate)
        if not ready:
            return {"text": "", "final": False}
        audio = np.concatenate(self._chunks)
        voiced_length = self._samples - self._silent
        self._chunks, self._samples, self._silent, self._speaking = [], 0, 0, False
        if voiced_length < self.config.minimum_utterance_seconds * self.sample_rate:
            return {"text": "", "final": True}
        if self.sample_rate != 16000:
            from math import gcd
            from scipy.signal import resample_poly
            divisor = gcd(self.sample_rate, 16000)
            audio = resample_poly(audio, 16000 // divisor, self.sample_rate // divisor).astype(np.float32)
        if self.audio_device is not None:
            import torch
            # CUDA Tensor 使 Whisper 的 STFT/梅尔特征和模型一起在 GPU 上执行。
            audio = torch.from_numpy(audio).to(self.audio_device)
        result = self.model.transcribe(audio, language=self.config.language, task="transcribe", fp16=True,
                                       temperature=0.0, beam_size=1, condition_on_previous_text=False,
                                       no_speech_threshold=self.config.no_speech_threshold, verbose=None)
        text = result.get("text", "")
        if not isinstance(text, str):
            raise ValueError("Whisper 未返回有效文本")
        segments = result.get("segments", [])
        if any(not math.isfinite(segment.get("no_speech_prob", 1))
               or not math.isfinite(segment.get("avg_logprob", 0)) for segment in segments):
            raise RuntimeError("Whisper 返回非有限概率，请检查音频特征和 CUDA 推理环境")
        if not segments or all(segment.get("no_speech_prob", 1) >= self.config.no_speech_threshold for segment in segments):
            text = ""
        return {"text": text.strip(), "final": True}
