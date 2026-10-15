"""模型真实前向检查；不打开摄像头、麦克风、串口或 GPIO。"""


def check_acceleration(config):
    reports = []
    from JETSON.Vision.engine import FaceEngine
    face = FaceEngine(config.face)
    try:
        if config.face.backend != "tensorrt":
            raise RuntimeError("当前人脸选择了 CPU 调试后端，不能通过 GPU 推理检查")
        import numpy as np
        face.load()
        size = config.face.inference_width
        face.detector.setInputSize((size, size))
        face.detector.detect(np.zeros((size, size, 3), dtype=np.uint8))
        feature = face.recognizer.feature(np.zeros((112, 112, 3), dtype=np.uint8))
        from JETSON.Vision.features import normalized_feature
        normalized_feature(feature)
        reports.append({"component": "face", "ok": True, "backend": config.face.backend,
                        "precision": config.face.precision, "dla_core": config.face.dla_core,
                        "dla_gpu_fallback": config.face.dla_gpu_fallback})
    except Exception as exc:
        reports.append({"component": "face", "ok": False, "error": str(exc)})
    finally:
        face.close()
    if config.voice.backend == "xfyun":
        reports.append({"component": "voice", "ok": True, "backend": "xfyun", "skipped": True, "note": "云端识别不需要本地 GPU 模型；此检查不验证云端授权和识别效果"})
        return {"ok": all(report["ok"] for report in reports), "results": reports}
    try:
        if config.voice.backend != "whisper_cuda":
            raise RuntimeError("当前语音选择了 CPU 调试后端，不能通过 GPU 推理检查")
        import numpy as np
        from JETSON.Voice.whisper_recognizer import WhisperRecognizerFactory
        voice = WhisperRecognizerFactory()(config.voice, 16000)
        voice.model.transcribe(np.zeros(16000, dtype=np.float32), language=config.voice.language,
                               fp16=True, temperature=0.0, beam_size=1, verbose=None)
        reports.append({"component": "voice", "ok": True, "backend": config.voice.backend,
                        "device": str(voice.model.device)})
    except Exception as exc:
        reports.append({"component": "voice", "ok": False, "error": str(exc)})
    return {"ok": all(report["ok"] for report in reports), "results": reports}
