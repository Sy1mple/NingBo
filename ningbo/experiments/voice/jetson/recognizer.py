"""Vosk 本地识别适配，模型按目录缓存，每次聆听创建独立解码器。"""
import json
from pathlib import Path


class VoskRecognizer:
    def __init__(self, decoder):
        self.decoder = decoder

    def process(self, pcm):
        final = self.decoder.AcceptWaveform(pcm)
        result = json.loads(self.decoder.Result() if final else self.decoder.PartialResult())
        text = result.get("text" if final else "partial", "")
        if not isinstance(text, str):
            raise ValueError("语音识别器未返回有效文本")
        return {"text": text.strip(), "final": bool(final)}


class VoskRecognizerFactory:
    def __init__(self):
        self._model = None
        self._path = None

    def __call__(self, config, sample_rate):
        from vosk import Model, KaldiRecognizer
        path = Path(config.model_path).resolve()
        if not (path / "am" / "final.mdl").is_file():
            raise FileNotFoundError(f"中文语音模型目录无效: {path}；请按 README 安装模型")
        if self._model is None or self._path != path:
            self._model = Model(str(path))
            self._path = path
        return VoskRecognizer(KaldiRecognizer(self._model, sample_rate))


class RecognizerFactory:
    """后端必须明确配置；CUDA 不可用时不会改成 Vosk。"""
    def __init__(self):
        self._factories = {}

    def __call__(self, config, sample_rate):
        if config.backend not in self._factories:
            if config.backend == "xfyun":
                from experiments.voice.jetson.xfyun_recognizer import XfyunRecognizer
                self._factories[config.backend] = XfyunRecognizer
            elif config.backend == "whisper_cuda":
                from experiments.voice.jetson.whisper_recognizer import WhisperRecognizerFactory
                self._factories[config.backend] = WhisperRecognizerFactory()
            elif config.backend == "vosk":
                self._factories[config.backend] = VoskRecognizerFactory()
            else:
                raise ValueError("未知语音推理后端")
        return self._factories[config.backend](config, sample_rate)
