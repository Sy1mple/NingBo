"""单个线程独占 USB 摄像头，浏览器及识别程序共享最新帧。"""
import threading
import time
from .camera import UsbCamera


class CameraStream:
    def __init__(self, config, hub, camera_factory=None, module=None, join_timeout=5.0):
        self.config, self.hub = config, hub
        self.camera_factory = camera_factory or UsbCamera
        self.module = module
        self.join_timeout = join_timeout
        self._lock = threading.RLock()
        self._lifecycle = threading.RLock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._worker = None
        self._frame = self._error = None
        self._frame_time = None

    def start(self, cancel_event=None):
        with self._lifecycle:
            if self._worker is not None and self._worker.is_alive():
                if self._stop.is_set():
                    raise RuntimeError('上一次摄像头采集尚未结束，不能重复打开设备')
                return
            if self.hub.closed:
                raise RuntimeError('预览服务已关闭')
            self._stop.clear()
            self._ready.clear()
            with self._lock:
                self._frame = self._error = self._frame_time = None
            self.hub.set_state('connecting', '正在连接 USB 摄像头')
            self._worker = threading.Thread(target=self._run, name='usb-camera-stream', daemon=True)
            try:
                self._worker.start()
            except Exception as exc:
                self._worker = None
                self._error = str(exc)
                self.hub.set_state('error', '摄像头采集线程启动失败')
                raise
            deadline = time.monotonic() + 10
            while not self._ready.wait(.05):
                if cancel_event is not None and cancel_event.is_set():
                    self._stop.set()
                    raise InterruptedError('摄像头启动已取消')
                if time.monotonic() >= deadline:
                    self._stop.set()
                    self.hub.set_state('error', '摄像头连接超时')
                    raise TimeoutError('摄像头连接超时；等待采集线程退出后才能重新启动')
            if cancel_event is not None and cancel_event.is_set():
                self._stop.set()
                raise InterruptedError('摄像头启动已取消')
            with self._lock:
                error = self._error
            if error:
                raise RuntimeError(error)

    def _run(self):
        camera, error = None, None
        try:
            module = self.module
            if module is None:
                import cv2 as module
            camera = self.camera_factory(self.config)
            camera.start()
            while not self._stop.is_set():
                started = time.monotonic()
                frame = camera.read()
                if self._stop.is_set():
                    break
                if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
                    raise RuntimeError('摄像头返回空图像或无效彩色帧')
                ok, jpeg = module.imencode('.jpg', frame, [module.IMWRITE_JPEG_QUALITY, 80])
                if not ok:
                    raise RuntimeError('摄像头预览 JPEG 编码失败')
                # 同一把锁让停止后不会重新发布迟到画面。
                with self._lock:
                    if self._stop.is_set():
                        break
                    self._frame = frame.copy()
                    self._frame_time = time.monotonic()
                    self.hub.publish(jpeg.tobytes(), frame.shape[1], frame.shape[0],
                                     getattr(camera, 'selected_device', self.config.device))
                self._ready.set()
                if self._stop.wait(max(0, 1 / self.config.fps - (time.monotonic() - started))):
                    break
        except Exception as exc:
            error = str(exc)
        finally:
            if camera is not None:
                try:
                    camera.close()  # 打开、读取、释放均发生在这个采集线程。
                except Exception as exc:
                    error = error or str(exc)
            with self._lock:
                self._frame, self._frame_time, self._error = None, None, error
                self.hub.set_state('error' if error else 'stopped', error or '摄像头已停止')
            self._ready.set()

    def read(self):
        """供以后的人脸处理复用；读取内存帧，不再打开第二个 VideoCapture。"""
        with self._lock:
            if self._error:
                raise RuntimeError(self._error)
            if self._frame is None or self._stop.is_set():
                raise RuntimeError('摄像头未启动或已停止')
            if time.monotonic() - self._frame_time > self.hub.stale_seconds:
                raise RuntimeError('摄像头画面已过期')
            return self._frame.copy()

    def snapshot(self):
        with self._lock:
            return {'worker_alive': bool(self._worker and self._worker.is_alive()),
                    'stopping': self._stop.is_set(), 'error': self._error}

    def close(self):
        with self._lifecycle:
            with self._lock:
                self._stop.set()
                self._frame = self._frame_time = None
                self.hub.set_state('error' if self._error else 'stopped', self._error or '摄像头已停止')
            if self._worker is not None:
                self._worker.join(self.join_timeout)
                if self._worker.is_alive():
                    raise RuntimeError('摄像头读取尚未返回；保留采集线程，禁止重复打开设备')
                self._worker = None
