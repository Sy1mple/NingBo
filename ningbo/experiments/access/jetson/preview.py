"""只监听本机的摄像头预览服务；多个浏览器共用最新帧，不打开摄像头。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

LOGGER = logging.getLogger(__name__)


class FrameHub:
    def __init__(self, stale_seconds=3.0, clock=time.monotonic):
        self.condition = threading.Condition()
        self.clock, self.stale_seconds = clock, stale_seconds
        self.jpeg = None
        self.sequence = 0
        self.frame_time = None
        self.state = 'idle'
        self.note = '等待启动摄像头'
        self.device = ''
        self.width = self.height = 0
        self.closed = False
        self.task = None

    def update_task(self, state):
        with self.condition:
            self.task = {key: state.get(key) for key in ('phase', 'mode', 'note', 'samples', 'person', 'error', 'recognized')}

    def set_state(self, state, note, device=''):
        with self.condition:
            self.state, self.note, self.device = state, note, str(device)
            if state != 'streaming':
                self.jpeg, self.frame_time = None, None
            self.condition.notify_all()

    def publish(self, jpeg, width, height, device=''):
        with self.condition:
            if self.closed:
                return
            self.jpeg = jpeg
            self.sequence += 1
            self.frame_time = self.clock()
            self.state, self.note = 'streaming', '摄像头已连接'
            self.width, self.height, self.device = width, height, str(device)
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            state, note = self.state, self.note
            age = None if self.frame_time is None else max(0, self.clock() - self.frame_time)
            if state == 'streaming' and (age is None or age > self.stale_seconds):
                state, note = 'stalled', '画面暂未更新，请检查摄像头连接'
            return {'service': 'ningbo-camera-preview', 'state': state, 'note': note,
                    'device': self.device, 'width': self.width, 'height': self.height,
                    'sequence': self.sequence, 'frame_age_seconds': age, 'task': self.task,
                    'capture_backend': getattr(self, 'capture_backend', 'opencv-v4l2'),
                    'capture_fps': getattr(self, 'capture_fps', None)}

    def wait_frame(self, after=0, timeout=1.0):
        with self.condition:
            self.condition.wait_for(lambda: self.closed or self.state != 'streaming' or self.sequence > after, timeout)
            if (self.closed or self.state != 'streaming' or self.jpeg is None
                    or self.frame_time is None or self.clock() - self.frame_time > self.stale_seconds):
                return None
            return (self.sequence, self.jpeg) if self.sequence > after else None

    def close(self):
        with self.condition:
            self.closed = True
            self.jpeg = None
            self.state, self.note = 'stopped', '预览已停止'
            self.condition.notify_all()


class CameraPreview:
    """默认只绑定 127.0.0.1；远端电脑通过 SSH 本地端口转发访问。"""
    def __init__(self, port=8765, hub=None, open_browser=True, browser_launcher=None):
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError('预览端口须为 0~65535 的整数')
        self.port, self.hub = port, hub or FrameHub()
        self.camera_control = None
        self.open_browser = open_browser
        if browser_launcher is None:
            from .desktop_browser import launch_local_firefox
            browser_launcher = launch_local_firefox
        self.browser_launcher = browser_launcher
        self.server = self.thread = self.browser_thread = None
        self._stop = threading.Event()
        self._browser_lock = threading.Lock()
        self._browser_done = False
        self._streams = threading.BoundedSemaphore(8)
        self._page = Path(__file__).with_name('preview.html').read_bytes()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/'

    def start(self, launch_browser=True):
        if self.server is not None:
            return
        if self.hub.closed:
            raise RuntimeError('预览服务已关闭，请重新创建实例')
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def send_preview_headers(self, code, content_type, size=None):
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-store, max-age=0')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                if size is not None:
                    self.send_header('Content-Length', str(size))
                self.end_headers()

            def do_GET(self):
                path = urlsplit(self.path).path
                try:
                    if path == '/':
                        self.send_preview_headers(200, 'text/html; charset=utf-8', len(owner._page))
                        self.wfile.write(owner._page)
                    elif path == '/status':
                        data = json.dumps(owner.hub.snapshot(), ensure_ascii=False).encode()
                        self.send_preview_headers(200, 'application/json; charset=utf-8', len(data))
                        self.wfile.write(data)
                    elif path == '/snapshot.jpg':
                        record = owner.hub.wait_frame(after=0, timeout=0.2)
                        if record is None:
                            self.send_preview_headers(503, 'text/plain', 0)
                        else:
                            sequence, jpeg = record
                            self.send_preview_headers(200, 'image/jpeg', len(jpeg))
                            self.wfile.write(jpeg)
                    elif path == '/stream.mjpg':
                        self.stream()
                    else:
                        self.send_preview_headers(404, 'text/plain', 0)
                except (OSError, TimeoutError):
                    pass  # 浏览器关闭或读流超时，不影响相机及其他客户端。

            def do_POST(self):
                path = urlsplit(self.path).path
                if path not in ('/iic/camera/lease', '/iic/camera/release'):
                    self.send_preview_headers(404, 'text/plain', 0)
                    return
                try:
                    # Non-browser local control: reject simple cross-origin form requests.
                    if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                        raise ValueError('需要 application/json')
                    if self.headers.get('Origin') is not None:
                        raise ValueError('仅供本机服务调用')
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0 < size <= 4096:
                        raise ValueError('请求体长度无效')
                    data = json.loads(self.rfile.read(size))
                    lease_id = data.get('lease_id','')
                    if not isinstance(lease_id,str) or re.fullmatch(r'[a-f0-9]{32}',lease_id) is None:
                        raise ValueError('lease_id 无效')
                    if owner._stop.is_set() or owner.camera_control is None:
                        raise RuntimeError('摄像头共享控制器不可用')
                    status = owner.camera_control.external_camera_lease(lease_id,release=path.endswith('/release'))
                    body = json.dumps({'ok':True,'camera':status},ensure_ascii=False).encode()
                    self.send_preview_headers(200,'application/json; charset=utf-8',len(body))
                    self.wfile.write(body)
                except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
                    body = json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False).encode()
                    self.send_preview_headers(400,'application/json; charset=utf-8',len(body))
                    self.wfile.write(body)
                except Exception as exc:
                    body = json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False).encode()
                    self.send_preview_headers(503,'application/json; charset=utf-8',len(body))
                    self.wfile.write(body)

            def stream(self):
                if not owner._streams.acquire(blocking=False):
                    self.send_preview_headers(503, 'text/plain', 0)
                    return
                try:
                    if owner.hub.snapshot()['state'] != 'streaming':
                        self.send_preview_headers(503, 'text/plain', 0)
                        return
                    self.send_preview_headers(200, 'multipart/x-mixed-replace; boundary=frame')
                    sequence = 0
                    while not owner._stop.is_set():
                        record = owner.hub.wait_frame(sequence)
                        if record is None:
                            if owner.hub.snapshot()['state'] != 'streaming':
                                break
                            continue
                        sequence, jpeg = record
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                         + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n')
                        self.wfile.flush()
                finally:
                    owner._streams.release()

        server = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        server.daemon_threads = True
        self.server, self.port = server, server.server_address[1]
        self.thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.2),
                                       name='camera-preview-http', daemon=True)
        try:
            self.thread.start()
            if launch_browser:
                self.open_local_browser()
        except Exception:
            self.stop()
            raise
        LOGGER.info('摄像头预览：%s；SSH 电脑使用本地端口转发访问同一路画面', self.url)

    def open_local_browser(self):
        """门禁模式仅在摄像头启动后调用；空闲 HTTP 服务不拉起浏览器。"""
        with self._browser_lock:
            if (not self.open_browser or self.server is None or self._stop.is_set() or self._browser_done
                    or self.browser_thread is not None and self.browser_thread.is_alive()):
                return
            self.browser_thread = threading.Thread(target=self._open_local_browser,
                                                   name='camera-preview-browser', daemon=True)
            self.browser_thread.start()

    def _open_local_browser(self):
        while not self._stop.is_set():
            try:
                result = self.browser_launcher(self.url)
                if result is not None:
                    self._browser_done = True
                    return
            except Exception:
                LOGGER.exception('本地浏览器启动失败，稍后重试；远程预览仍可用')
            if self._stop.wait(10):
                break

    def stop(self):
        self._stop.set()
        if self.camera_control is not None:
            try:
                self.camera_control.shutdown_shared_camera()
            except Exception:
                LOGGER.exception('共享摄像头清理失败')
        self.hub.close()
        server, self.server = self.server, None
        if server is not None:
            if self.thread is not None and self.thread.is_alive():
                server.shutdown()
            server.server_close()
        for thread in (self.thread, self.browser_thread):
            if thread is not None and thread.is_alive():
                thread.join(3)
