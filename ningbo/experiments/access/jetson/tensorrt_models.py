"""YuNet 后处理和 SFace 对齐。神经网络前向只调用 TensorRT。"""
from experiments.access.jetson.tensorrt_runner import TensorRTRunner


def decode_yunet(outputs, width, height, config, cv):
    import numpy as np
    candidates = []
    for stride in (8, 16, 32):
        rows, cols = height // stride, width // stride
        cls = outputs[f'cls_{stride}'].reshape(-1)
        obj = outputs[f'obj_{stride}'].reshape(-1)
        boxes = outputs[f'bbox_{stride}'].reshape(-1, 4)
        points = outputs[f'kps_{stride}'].reshape(-1, 10)
        if any(len(value) != rows * cols for value in (cls, obj, boxes, points)):
            raise ValueError("YuNet 输出尺寸不匹配")
        scores = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        indices = np.flatnonzero(scores >= config.detector_score)
        for index in indices:
            row, col = divmod(int(index), cols)
            box = boxes[index]
            center = (np.array([col, row]) + box[:2]) * stride
            size = np.exp(box[2:]) * stride
            landmarks = (points[index].reshape(5, 2) + [col, row]) * stride
            face = np.r_[center - size / 2, size, landmarks.reshape(-1), scores[index]].astype(np.float32)
            if np.isfinite(face).all():
                candidates.append(face)
    if not candidates:
        return None
    faces = np.stack(candidates)
    keep = cv.dnn.NMSBoxes(faces[:, :4].astype(int).tolist(), faces[:, 14].astype(float).tolist(),
                           config.detector_score, config.detector_nms, top_k=config.detector_top_k)
    return faces[np.asarray(keep).reshape(-1).astype(int)] if len(keep) else None


def align_face(frame, face, cv):
    import numpy as np
    source = np.asarray(face[4:14], dtype=np.float64).reshape(5, 2)
    target = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                       [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float64)
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    x, y = source - source_mean, target - target_mean
    u, singular, vt = np.linalg.svd(y.T @ x / 5)
    signs = np.ones(2)
    if np.linalg.det(u @ vt) < 0:
        signs[-1] = -1
    variance = float((x * x).sum() / 5)
    if variance <= 1e-6:
        raise ValueError("人脸关键点退化，不能对齐")
    rotation = u @ np.diag(signs) @ vt
    scale = float(singular @ signs) / variance
    transform = np.c_[scale * rotation, target_mean - scale * rotation @ source_mean].astype(np.float32)
    return cv.warpAffine(frame, transform, (112, 112), flags=cv.INTER_LINEAR)


class TensorRTDetector:
    def __init__(self, config, cv):
        self.config, self.cv = config, cv
        self.size = config.detector_input_size
        self.runner = TensorRTRunner(config.detector_path, (1, 3, self.size, self.size), config)

    def setInputSize(self, size):
        if max(size) > self.size:
            raise ValueError("摄像头缩放尺寸超出检测引擎；请调整 inference_width 或摄像头横纵方向")

    def detect(self, image):
        h, w = image.shape[:2]
        padded = self.cv.copyMakeBorder(image, 0, self.size - h, 0, self.size - w, self.cv.BORDER_CONSTANT, value=0)
        outputs = self.runner.infer(self.cv.dnn.blobFromImage(padded))
        return 1, decode_yunet(outputs, self.size, self.size, self.config, self.cv)

    def close(self):
        self.runner.close()


class TensorRTRecognizer:
    def __init__(self, config, cv):
        self.cv = cv
        self.runner = TensorRTRunner(config.recognizer_path, (1, 3, 112, 112), config)

    def alignCrop(self, frame, face):
        return align_face(frame, face, self.cv)

    def feature(self, aligned):
        outputs = self.runner.infer(self.cv.dnn.blobFromImage(aligned, swapRB=True))
        if len(outputs) != 1:
            raise ValueError("SFace 必须返回一路特征")
        return next(iter(outputs.values()))

    def close(self):
        self.runner.close()
