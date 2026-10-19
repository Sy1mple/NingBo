"""Access model real-forward check. Does not open a camera or GPIO."""

def check_acceleration(config):
    if config.face.backend == 'xfyun':
        return {'component': 'face', 'ok': True, 'backend': 'xfyun', 'skipped': True,
                'note': '讯飞云端人脸无需本地模型/GPU；本检查不调用 API，不验证密钥、免费包或识别效果'}
    reports = []
    from experiments.access.jetson.engine import FaceEngine
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
        from experiments.access.jetson.features import normalized_feature
        normalized_feature(feature)
        reports.append({"component": "face", "ok": True, "backend": config.face.backend,
                        "precision": config.face.precision, "dla_core": config.face.dla_core,
                        "dla_gpu_fallback": config.face.dla_gpu_fallback})
    except Exception as exc:
        reports.append({"component": "face", "ok": False, "error": str(exc)})
    finally:
        face.close()
    return reports[0]
