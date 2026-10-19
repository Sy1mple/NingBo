"""access hardware and model defaults."""
from dataclasses import dataclass, field
from pathlib import Path
from paths import PROJECT_ROOT


@dataclass(frozen=True)
class CameraConfig:
    device: object = "auto"  # 扫描 USB 图像采集节点；可用 --camera-device 显式指定。
    width: int = 640
    height: int = 480
    fps: int = 15
    preview_enabled: bool = True
    preview_port: int = 8765
    preview_local_browser: bool = True


def _face_model_path(filename):
    return str(Path(__file__).resolve().parent / "models" / filename)


def _face_database_path():
    return str(PROJECT_ROOT / "data" / "faces_xfyun.sqlite3")


@dataclass(frozen=True)
class FaceConfig:
    enabled: bool = True
    backend: str = "xfyun"
    credentials_file: str = field(default_factory=lambda: str(PROJECT_ROOT / ".env.face"))
    usage_file: str = field(default_factory=lambda: str(PROJECT_ROOT / "data" / "face_usage.sqlite3"))
    api_timeout: float = 2.0
    api_interval: float = 1.1  # 所有检测/搜索/录入请求共用限速。
    cloud_threshold: float = 0.80  # 讯飞比对为 0~1 分，独立于 SFace 阈值。
    cloud_margin: float = 0.05
    cloud_round_interval: float = 3.0
    cloud_confirmations: int = 2
    verification_timeout: float = 20.0  # 一次按钮验证的时限，不持续后台识别。
    verification_indicator_seconds: float = 2.0  # 验证成功绿灯、失败红灯的显示时长。
    failure_buzzer_seconds: float = 2.0
    cuda_device: int = 0
    precision: str = "fp16"
    dla_core: int = None  # None 使用 GPU；0/1 使用 DLA，须在 Jetson 上构建引擎。
    dla_gpu_fallback: bool = True  # DLA 不支持的层交给 GPU，绝不交给 CPU。
    engine_cache: str = field(default_factory=lambda: str(PROJECT_ROOT / "data" / "engines"))
    workspace_mb: int = 256
    detector_path: str = field(default_factory=lambda: _face_model_path("face_detection_yunet_2023mar.onnx"))
    recognizer_path: str = field(default_factory=lambda: _face_model_path("face_recognition_sface_2021dec.onnx"))
    database_path: str = field(default_factory=_face_database_path)
    detector_score: float = 0.85
    detector_nms: float = 0.3
    detector_top_k: int = 5000
    detector_input_size: int = 640  # 官方 YuNet ONNX 的固定输入，不能任意改成 320。
    inference_width: int = 640
    feature_dimension: int = 128
    similarity_threshold: float = 0.363  # OpenCV 官方示例分界，部署时用真实摄像头标定。
    ambiguity_margin: float = 0.05
    minimum_face_pixels: int = 80
    minimum_sharpness: float = 50.0
    processing_interval: float = 0.25
    consecutive_matches: int = 3
    enroll_samples: int = 5
    enroll_interval: float = 0.3
    enroll_timeout: float = 20.0
    enroll_consistency: float = 0.6
    duplicate_threshold: float = 0.6
    enroll_name: str = ""  # 未填时自动生成人员编号。
    enroll_indicator_seconds: float = 2.0
    enroll_preview_seconds: float = 3.0  # 显示画面，给人员对准镜头的时间；随后再取录入照片。
    result_queue_size: int = 16
    # 当前业务：匹配成功后门锁通电 2 秒。
    unlock_on_match: bool = True
    unlock_seconds: float = 2.0


@dataclass(frozen=True)
class LockConfig:
    pin: int = 29
    active_high: bool = True
    enabled: bool = True

import math


def validate(self):
    camera = self.camera
    if (isinstance(camera.device, bool) or not isinstance(camera.device, (int, str))
            or (isinstance(camera.device, int) and camera.device < 0)
            or (isinstance(camera.device, str) and (not camera.device.strip() or "://" in camera.device))):
        raise ValueError("USB 摄像头须为 auto、非负编号或本地设备路径")
    for name in ("width", "height", "fps"):
        value = getattr(camera, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"camera.{name} 必须是正整数")
    if not isinstance(camera.preview_enabled, bool) or not isinstance(camera.preview_local_browser, bool):
        raise ValueError('摄像头预览和本地浏览器开关须为布尔值')
    if isinstance(camera.preview_port, bool) or not isinstance(camera.preview_port, int) or not 1 <= camera.preview_port <= 65535:
        raise ValueError('摄像头预览端口须为 1~65535 的整数')
    face = self.face
    if (isinstance(face.enroll_preview_seconds, bool) or not math.isfinite(face.enroll_preview_seconds)
            or not 0 <= face.enroll_preview_seconds <= 10):
        raise ValueError('录入准备预览时间须为 0~10 秒')
    if face.backend not in ("xfyun", "tensorrt", "opencv_cpu"):
        raise ValueError("人脸后端须为 xfyun、tensorrt 或 opencv_cpu")
    if not face.credentials_file or not face.usage_file:
        raise ValueError("人脸 API 密钥和用量文件路径不能为空")
    if (not math.isfinite(face.api_timeout) or not 0 < face.api_timeout < self.thread_join_timeout
            or not math.isfinite(face.api_interval) or face.api_interval < 1.0):
        raise ValueError("人脸 API 超时须小于线程关闭超时，请求间隔须至少为 1 秒")
    if any(isinstance(v, bool) or not math.isfinite(v) or not 0 < v < 1
           for v in (face.cloud_threshold, face.cloud_margin)):
        raise ValueError("人脸云端阈值和候选差值须在 0~1 分之间")
    if (not math.isfinite(face.cloud_round_interval) or face.cloud_round_interval < 1
            or isinstance(face.cloud_confirmations, bool) or not isinstance(face.cloud_confirmations, int)
            or face.cloud_confirmations < 1):
        raise ValueError("云端识别周期须至少 1 秒，连续确认次数须为正整数")
    if isinstance(face.cuda_device, bool) or not isinstance(face.cuda_device, int) or face.cuda_device < 0:
        raise ValueError("face.cuda_device 必须为非负整数")
    if face.precision not in ("fp16", "fp32"):
        raise ValueError("TensorRT 精度须为 fp16 或 fp32")
    if face.dla_core is not None and (isinstance(face.dla_core, bool) or face.dla_core not in (0, 1)):
        raise ValueError("Xavier NX 的 DLA 核编号须为 0、1 或 None")
    if face.dla_core is not None and (face.precision != "fp16" or face.backend != "tensorrt"):
        raise ValueError("DLA 当前需使用 TensorRT FP16")
    if not isinstance(face.dla_gpu_fallback, bool) or not face.engine_cache:
        raise ValueError("DLA GPU 兼容配置或引擎缓存路径无效")
    if isinstance(face.workspace_mb, bool) or not isinstance(face.workspace_mb, int) or face.workspace_mb < 16:
        raise ValueError("TensorRT 工作区须为至少 16 MB 的整数")
    if not isinstance(face.enabled, bool) or not isinstance(face.unlock_on_match, bool):
        raise ValueError("人脸启用和自动开锁配置必须为布尔值")
    if not all((face.detector_path, face.recognizer_path, face.database_path)):
        raise ValueError("人脸模型和人脸库路径不能为空")
    for name in ("detector_top_k", "detector_input_size", "inference_width", "minimum_face_pixels", "consecutive_matches",
                 "enroll_samples", "result_queue_size"):
        value = getattr(face, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"face.{name} 必须是正整数")
    if face.detector_input_size % 32 or face.inference_width > face.detector_input_size:
        raise ValueError("人脸引擎输入须为 32 的倍数且不小于缩放宽度，当前模型原生输入为 640")
    if face.feature_dimension != 128:
        raise ValueError("当前 SFace 模型使用 128 维特征")
    for name in ("detector_score", "detector_nms", "similarity_threshold", "ambiguity_margin",
                 "enroll_consistency", "duplicate_threshold"):
        value = getattr(face, name)
        if not math.isfinite(value) or not 0 < value < 1:
            raise ValueError(f"face.{name} 必须在 0~1 之间")
    for name in ("processing_interval", "enroll_interval", "enroll_timeout", "enroll_indicator_seconds", "unlock_seconds",
                 "verification_timeout", "verification_indicator_seconds", "failure_buzzer_seconds"):
        value = getattr(face, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"face.{name} 必须是有限正数")
    if not math.isfinite(face.minimum_sharpness) or face.minimum_sharpness < 0:
        raise ValueError("人脸清晰度阈值必须是有限非负数")
    if face.enroll_timeout <= face.enroll_interval * (face.enroll_samples - 1):
        raise ValueError("人脸录入超时须大于多帧采集所需间隔")
    if not isinstance(face.enroll_name, str) or len(face.enroll_name) > 64:
        raise ValueError("录入姓名须为不超过 64 字的字符串")
    if face.unlock_on_match and not self.lock.enabled:
        raise ValueError("自动开锁需要启用电磁锁驱动")


def pin_assignments(config):
    return [(config.lock.pin, "lock")] if config.lock.enabled else []
