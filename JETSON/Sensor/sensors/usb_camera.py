"""USB 摄像头输入。连接、读取和释放均由同一个视觉工作线程完成。"""
from pathlib import Path
import logging
import os
import struct
import sys

LOGGER = logging.getLogger(__name__)


def _capabilities(path):
    import fcntl
    fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    try:
        data = bytearray(104)  # struct v4l2_capability, Linux UAPI.
        fcntl.ioctl(fd, 0x80685600, data, True)  # VIDIOC_QUERYCAP
        driver, card, bus, version, caps, device_caps, _, _, _ = struct.unpack('=16s32s32sIII3I', data)
        caps = device_caps if caps & 0x80000000 else caps
        decode = lambda value: value.split(b'\0', 1)[0].decode('utf-8', errors='replace')
        return {'name': decode(card), 'driver': decode(driver), 'bus': decode(bus),
                'usb': decode(bus).startswith('usb-'), 'capture': bool(caps & (1 | 0x1000))}
    finally:
        os.close(fd)


def camera_nodes():
    if not sys.platform.startswith("linux"):
        return []
    paths = (sorted(Path('/dev/v4l/by-id').glob('*video*'))
             + sorted(Path('/dev/v4l/by-path').glob('*video*')) + sorted(Path('/dev').glob('video[0-9]*')))
    nodes, seen = [], set()
    for path in paths:
        target = str(path.resolve())
        if target in seen:
            continue
        seen.add(target)
        info = {'path': str(path), 'target': target}
        try:
            info.update(_capabilities(path))
        except OSError as exc:
            info.update(capture=False, usb=False, error=str(exc))
        nodes.append(info)
    return nodes


class UsbCamera:
    def __init__(self, config, module=None):
        self.config = config
        self.module = module
        self._capture = None
        self._first_frame = None
        self.selected_device = None

    def start(self):
        if self._capture is not None:
            return
        if self.module is None:
            import cv2
            self.module = cv2
        source = self.config.device
        if source == 'auto':
            candidates = [node for node in camera_nodes() if node.get('usb') and node.get('capture')]
            if not candidates:
                raise OSError('未发现可采集图像的 USB 摄像头；请检查 USB 连接或指定 --camera-device')
            if len({node['bus'] for node in candidates}) > 1:
                raise OSError('发现多台 USB 摄像头，请使用 --camera-device 指定：'
                              + ', '.join(node['path'] for node in candidates))
            sources = [node['path'] for node in candidates]
        else:
            sources = [source]
        if isinstance(source, str) and source != 'auto':
            if not Path(source).exists():
                raise FileNotFoundError(f"USB 摄像头路径不存在: {source}")
        backend = self.module.CAP_V4L2 if sys.platform.startswith("linux") else self.module.CAP_ANY
        errors = []
        for source in sources:
            capture = self.module.VideoCapture(source, backend)
            try:
                if not capture.isOpened():
                    raise OSError('无法打开')
                capture.set(self.module.CAP_PROP_FRAME_WIDTH, self.config.width)
                capture.set(self.module.CAP_PROP_FRAME_HEIGHT, self.config.height)
                capture.set(self.module.CAP_PROP_FPS, self.config.fps)
                ok, frame = capture.read()
                if not ok or frame is None or frame.size == 0:
                    raise OSError('未读到有效图像帧')
                self._capture, self._first_frame = capture, frame
                self.selected_device = source
                LOGGER.info('USB 摄像头已选择：%s；首帧 %sx%s', source, frame.shape[1], frame.shape[0])
                return
            except Exception as exc:
                capture.release()
                errors.append(str(source) + ': ' + str(exc))
        raise OSError('USB 摄像头无法采集：' + '; '.join(errors))

    def read(self):
        if self._capture is None:
            raise RuntimeError("请先启动 USB 摄像头")
        if self._first_frame is not None:
            frame, self._first_frame = self._first_frame, None
            return frame
        ok, frame = self._capture.read()
        if not ok or frame is None or frame.size == 0:
            raise OSError("USB 摄像头本次读取失败或已断开")
        return frame

    def close(self):
        capture, self._capture = self._capture, None
        self._first_frame = None
        self.selected_device = None
        if capture is not None:
            capture.release()
