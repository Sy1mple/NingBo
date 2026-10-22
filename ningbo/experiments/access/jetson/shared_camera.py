"""Named leases over the original NingBo capture thread; one USB owner."""
import logging
import threading
import time
from .camera_stream import CameraStream

LOG = logging.getLogger(__name__)


class SharedCameraStream:
    def __init__(self, config, hub, camera_factory=None, join_timeout=5.0, stream=None):
        self.stream = stream or CameraStream(config, hub, camera_factory=camera_factory, join_timeout=join_timeout)
        self.lock = threading.RLock()
        self.leases = {}
        self.closed = False
        self.stop_reaper = threading.Event()
        self.reaper = None

    def acquire(self, owner, ttl=None, cancel_event=None):
        with self.lock:
            if self.closed:
                raise RuntimeError('摄像头管理器已关闭')
            previous = self.leases.get(owner)
            existed = owner in self.leases
            self.leases[owner] = None if ttl is None else time.monotonic()+ttl
            try:
                self.stream.start(cancel_event=cancel_event)
            except Exception:
                if existed:
                    self.leases[owner] = previous
                else:
                    self.leases.pop(owner,None)
                raise
            if ttl is not None and self.reaper is None:
                self.reaper = threading.Thread(target=self._expire,daemon=True,name='camera-lease-expiry')
                self.reaper.start()

    def release(self, owner):
        with self.lock:
            self.leases.pop(owner,None)
            if not self.leases:
                self.stream.close()

    def start(self, cancel_event=None):
        self.acquire('face',cancel_event=cancel_event)

    def read(self):
        return self.stream.read()

    def close(self):
        # Existing face-task cleanup releases only its own lease.
        self.release('face')

    def snapshot(self):
        with self.lock:
            return {**self.stream.snapshot(),'face_lease':'face' in self.leases,
                    'subscribers':list(self.leases)}

    def _expire(self):
        while not self.stop_reaper.wait(0.5):
            with self.lock:
                now = time.monotonic()
                expired = [owner for owner,deadline in self.leases.items() if deadline is not None and deadline<=now]
                for owner in expired:
                    self.leases.pop(owner,None)
                if expired and not self.leases:
                    try:
                        self.stream.close()
                    except Exception:
                        LOG.exception('过期摄像头订阅释放失败，保留原采集线程，禁止重复打开')

    def shutdown(self):
        self.stop_reaper.set()
        with self.lock:
            self.closed = True
            self.leases.clear()
            self.stream.close()
        if self.reaper is not None:
            self.reaper.join(1)
