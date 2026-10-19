"""独立摄像头预览入口；不启动串口屏、GPIO、人脸识别或云端请求。"""
import argparse
import logging
import signal
import threading

from experiments.access.jetson.config import CameraConfig
from experiments.access.jetson.camera_stream import CameraStream
from experiments.access.jetson.preview import CameraPreview


def main(argv=None):
    parser = argparse.ArgumentParser(description='USB 摄像头网页预览：开发板火狐和 SSH 电脑可同时观看')
    parser.add_argument('--camera-device', '--device', default='auto', help='auto、摄像头编号或本地设备路径')
    parser.add_argument('--port', type=int, default=8765, help='本机预览端口，默认 8765')
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--fps', type=int, default=15)
    parser.add_argument('--no-local-browser', action='store_true', help='不自动打开开发板桌面的 Firefox')
    parser.add_argument('--check-config', action='store_true', help='只检查参数，不打开摄像头、端口或浏览器')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('预览端口须为 1~65535')
    if not (1 <= args.width <= 4096 and 1 <= args.height <= 4096 and 1 <= args.fps <= 60):
        parser.error('宽高须为 1~4096，FPS 须为 1~60')
    device = args.camera_device.strip()
    if not device or '://' in device or device.startswith('-'):
        parser.error('摄像头须为 auto、非负编号或本地设备路径')
    device = int(device) if device.isdecimal() else device
    config = CameraConfig(device=device, width=args.width, height=args.height, fps=args.fps)
    if args.check_config:
        print(f'Camera: {config.device}; {config.width}x{config.height}; {config.fps} FPS')
        print(f'Preview: http://127.0.0.1:{args.port}/; local Firefox: {not args.no_local_browser}')
        print('Configuration OK; no camera, GPIO, serial, API, browser or network port opened.')
        return 0
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    preview = CameraPreview(port=args.port, open_browser=not args.no_local_browser)
    stream = CameraStream(config, preview.hub)
    shutdown = threading.Event()
    previous = {}
    result = 0
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: shutdown.set())
        preview.start()
        print('本地预览：' + preview.url, flush=True)
        print(f'SSH 电脑：ssh -L {args.port}:127.0.0.1:{args.port} 用户名@开发板IP；'
              f'然后在电脑浏览器打开 {preview.url}', flush=True)
        stream.start()
        while not shutdown.wait(0.2):
            state = stream.snapshot()
            if state['error']:
                raise RuntimeError(state['error'])
            if not state['worker_alive']:
                raise RuntimeError('摄像头采集线程已退出')
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.getLogger(__name__).exception('摄像头预览运行失败')
        result = 1
    finally:
        try:
            stream.close()
        except Exception:
            logging.getLogger(__name__).exception('摄像头尚未完全释放')
            result = 1
        finally:
            preview.stop()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
