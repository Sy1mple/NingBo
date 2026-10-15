"""YuNet 人脸检测与 SFace 特征提取；默认使用 TensorRT GPU/DLA。"""
from .features import model_signature, normalized_feature


class FaceEngine:
    def __init__(self, config):
        self.config = config
        self.cv = None
        self.detector = None
        self.recognizer = None
        self.signature = None

    def load(self):
        if self.recognizer is not None and self.detector is not None:
            return
        import cv2
        self.cv = cv2
        if self.config.backend == "opencv_cpu" and (not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF")):
            raise RuntimeError("OpenCV 缺少人脸 DNN 接口，请安装 requirements.txt 中的 OpenCV 4.x")
        self.signature = model_signature(self.config)
        if self.config.backend == "tensorrt":
            from .tensorrt_models import TensorRTDetector, TensorRTRecognizer
            try:
                self.detector = TensorRTDetector(self.config, cv2)
                self.recognizer = TensorRTRecognizer(self.config, cv2)
            except Exception:
                self.close()
                raise
            return
        self.detector = cv2.FaceDetectorYN.create(
            self.config.detector_path, "", (320, 320), self.config.detector_score,
            self.config.detector_nms, self.config.detector_top_k,
        )
        self.recognizer = cv2.FaceRecognizerSF.create(self.config.recognizer_path, "")

    def analyze(self, frame):
        self.load()
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) <= 0:
            raise ValueError("摄像头须返回 BGR 彩色图像")
        height, width = frame.shape[:2]
        scale = min(1.0, self.config.inference_width / max(width, height))
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = self.cv.resize(frame, size) if scale < 1 else frame
        self.detector.setInputSize(size)
        _, faces = self.detector.detect(image)
        count = 0 if faces is None else len(faces)
        if count != 1:
            return {"valid": False, "face_count": count, "note": "请保持一人在画面中" if count else "等待人脸进入画面"}
        face = faces[0].copy()
        face[:14] /= scale
        if min(float(face[2]), float(face[3])) < self.config.minimum_face_pixels:
            return {"valid": False, "face_count": 1, "note": "人脸太小，请靠近摄像头"}
        aligned = self.recognizer.alignCrop(frame, face)
        gray = self.cv.cvtColor(aligned, self.cv.COLOR_BGR2GRAY)
        sharpness = float(self.cv.Laplacian(gray, self.cv.CV_64F).var())
        if sharpness < self.config.minimum_sharpness:
            return {"valid": False, "face_count": 1, "note": "人脸画面模糊，请保持稳定"}
        feature = normalized_feature(self.recognizer.feature(aligned), self.config.feature_dimension)
        return {"valid": True, "face_count": 1, "feature": feature, "sharpness": sharpness, "note": "有效人脸"}

    def close(self):
        for model in (self.detector, self.recognizer):
            if model is not None and hasattr(model, "close"):
                model.close()
        self.detector = self.recognizer = None
