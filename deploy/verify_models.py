"""验证打包模型；读取流式校验值，不运行模型或打开外设。"""
import hashlib
from pathlib import Path
import sys

MODELS = {
    'JETSON/Vision/models/face_detection_yunet_2023mar.onnx': '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4',
    'JETSON/Vision/models/face_recognition_sface_2021dec.onnx': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
    'JETSON/Voice/models/whisper-tiny.pt': '65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9',
}


def verify(root):
    for relative, expected in MODELS.items():
        path = root / relative
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError('模型文件不完整或已改变：' + relative)
        print('SHA256 OK: ' + relative)


if __name__ == '__main__':
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
