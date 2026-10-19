"""统一的人脸特征格式及模型指纹。"""
import hashlib
from pathlib import Path


def normalized_feature(value, dimension=128):
    import numpy as np
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    if vector.size != dimension or not np.isfinite(vector).all():
        raise ValueError("人脸特征维度或数值无效")
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-6:
        raise ValueError("人脸特征不能为零向量")
    return (vector / norm).astype("<f4")


def model_signature(config):
    signatures = []
    for name in (config.detector_path, config.recognizer_path):
        path = Path(name)
        if not path.is_file() or path.stat().st_size < 1024:
            raise FileNotFoundError(f"人脸 ONNX 模型缺失或无效: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        signatures.append(digest.hexdigest())
    return "sface-aligned112-v1:" + ":".join(signatures)
