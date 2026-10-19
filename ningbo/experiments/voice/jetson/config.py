"""voice hardware and model defaults."""
from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


def _voice_model_path():
    return str(Path(__file__).resolve().parent / "models" / "whisper-tiny.pt")


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = True
    backend: str = "xfyun"
    cuda_device: int = 0
    language: str = "zh"
    silence_rms: float = 0.015
    input_gain: float = 30.0  # AIMIC-M4 实测输入较小；分句及识别前放大并限幅。
    input_latency_seconds: float = 0.5  # 为 GPU 推理期间的采音回调保留缓冲。
    silence_seconds: float = 0.6
    minimum_utterance_seconds: float = 0.5
    maximum_utterance_seconds: float = 8.0
    no_speech_threshold: float = 0.6
    input_device: object = "auto"  # 自动扫描 USB 输入，优先 AIMIC-M4/AISPEECH，不依赖声卡编号。
    sample_rate: int = None  # None 使用麦克风支持的默认采样率。
    block_duration_ms: int = 40
    audio_queue_blocks: int = 128
    result_queue_size: int = 8
    read_timeout: float = 0.1
    partial_interval: float = 0.3
    show_partial: bool = True
    xfyun_endpoint: str = "wss://iat-api.xfyun.cn/v2/iat"
    xfyun_credentials_file: str = field(default_factory=lambda: str(PROJECT_ROOT / ".env.voice"))
    xfyun_usage_file: str = field(default_factory=lambda: str(PROJECT_ROOT / "data" / "voice_usage.sqlite3"))
    xfyun_daily_limit: int = 450  # 本项目保守上限；平台默认免费额度为每日 500 次。
    xfyun_connect_timeout: float = 3.0
    xfyun_final_timeout: float = 2.0
    xfyun_trigger_seconds: float = 0.12
    xfyun_max_seconds: float = 50.0  # 每个会话小于平台 60 秒限制。
    model_path: str = field(default_factory=_voice_model_path)
    text_encoding: str = "gb2312"  # 与实机文本控件的 0x01 GB2312 内码一致。
    text_words: int = 32       # 64 Byte，含字符串结束标记；屏幕控件长度设置为 64 Byte。

import math


def validate(self):
    voice = self.voice
    if voice.backend not in ("xfyun", "whisper_cuda", "vosk"):
        raise ValueError("语音后端须为 xfyun、whisper_cuda 或 vosk")
    if voice.xfyun_endpoint != "wss://iat-api.xfyun.cn/v2/iat":
        raise ValueError("讯飞接口须使用已核对的官方 WSS 地址")
    if isinstance(voice.xfyun_daily_limit, bool) or not isinstance(voice.xfyun_daily_limit, int) or not 1 <= voice.xfyun_daily_limit <= 450:
        raise ValueError("讯飞本项目每日调用上限须为 1~450 次")
    if (not voice.xfyun_credentials_file or not voice.xfyun_usage_file
            or any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in
                   (voice.xfyun_connect_timeout, voice.xfyun_final_timeout, voice.xfyun_trigger_seconds, voice.xfyun_max_seconds))
            or voice.xfyun_connect_timeout >= self.thread_join_timeout
            or voice.xfyun_final_timeout >= self.thread_join_timeout
            or not voice.xfyun_trigger_seconds < voice.xfyun_max_seconds <= 50):
        raise ValueError("讯飞配置路径、超时或会话长度无效")
    if isinstance(voice.cuda_device, bool) or not isinstance(voice.cuda_device, int) or voice.cuda_device < 0:
        raise ValueError("voice.cuda_device 必须为非负整数")
    if not voice.language or not isinstance(voice.language, str):
        raise ValueError("语音语言不能为空")
    if not math.isfinite(voice.silence_rms) or not 0 < voice.silence_rms < 1:
        raise ValueError("语音静音 RMS 阈值须在 0~1 之间")
    if isinstance(voice.input_latency_seconds, bool) or not math.isfinite(voice.input_latency_seconds) or not 0 < voice.input_latency_seconds <= 2:
        raise ValueError("语音采音缓冲时长须为 0~2 秒之间的有限正数")
    if isinstance(voice.input_gain, bool) or not math.isfinite(voice.input_gain) or not 0 < voice.input_gain <= 100:
        raise ValueError("语音输入增益须为 0~100 之间的有限正数")
    if any(not math.isfinite(value) or value <= 0 for value in
           (voice.silence_seconds, voice.minimum_utterance_seconds, voice.maximum_utterance_seconds)):
        raise ValueError("语音分句时长必须是有限正数")
    if not voice.minimum_utterance_seconds < voice.maximum_utterance_seconds <= 25:
        raise ValueError("语音最短分句须小于最长分句且最长不超过 25 秒")
    if not 0 < voice.no_speech_threshold < 1:
        raise ValueError("语音无讲话概率阈值须在 0~1 之间")
    if (isinstance(voice.input_device, bool) or not isinstance(voice.input_device, (int, str))
            or (isinstance(voice.input_device, int) and voice.input_device < 0)
            or (isinstance(voice.input_device, str) and not voice.input_device.strip())):
        raise ValueError("USB 麦克风设备须为 auto、非负编号或名称关键字")
    if voice.sample_rate is not None and (isinstance(voice.sample_rate, bool) or not isinstance(voice.sample_rate, int)
                                          or not 8000 <= voice.sample_rate <= 192000):
        raise ValueError("语音采样率须为 8000~192000 的整数或 None")
    for name, low, high in (("block_duration_ms", 10, 1000), ("audio_queue_blocks", 1, 256),
                            ("result_queue_size", 1, 256), ("text_words", 2, 126)):
        value = getattr(voice, name)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"voice.{name} 必须为 {low}~{high} 的整数")
    if voice.text_encoding not in ("gbk", "gb2312", "utf-16-be"):
        raise ValueError("屏幕语音文本编码须为 gbk、gb2312 或 utf-16-be")
    if not voice.model_path:
        raise ValueError("语音模型目录不能为空")
    if any(not math.isfinite(value) or value <= 0 for value in (voice.read_timeout, voice.partial_interval)):
        raise ValueError("语音读取超时与临时结果间隔必须为有限正数")
    if voice.read_timeout >= self.thread_join_timeout:
        raise ValueError("语音读取超时必须小于线程关闭超时")
