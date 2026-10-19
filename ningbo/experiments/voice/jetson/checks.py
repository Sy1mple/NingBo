"""Voice model real-forward check. Cloud authorization is not checked here."""

def check_acceleration(config):
    reports = []
    if config.voice.backend == "xfyun":
        reports.append({"component": "voice", "ok": True, "backend": "xfyun", "skipped": True, "note": "云端识别不需要本地 GPU 模型；此检查不验证云端授权和识别效果"})
        return reports[0]
    try:
        if config.voice.backend != "whisper_cuda":
            raise RuntimeError("当前语音选择了 CPU 调试后端，不能通过 GPU 推理检查")
        import numpy as np
        from experiments.voice.jetson.whisper_recognizer import WhisperRecognizerFactory
        voice = WhisperRecognizerFactory()(config.voice, 16000)
        voice.model.transcribe(np.zeros(16000, dtype=np.float32), language=config.voice.language,
                               fp16=True, temperature=0.0, beam_size=1, verbose=None)
        reports.append({"component": "voice", "ok": True, "backend": config.voice.backend,
                        "device": str(voice.model.device)})
    except Exception as exc:
        reports.append({"component": "voice", "ok": False, "error": str(exc)})
    return reports[0]
