"""在 SSH 电脑运行：建立预览隧道并自动打开这台电脑的默认浏览器。"""
import argparse
import json
import shutil
import socket
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser


def main(argv=None):
    parser = argparse.ArgumentParser(description='SSH 电脑摄像头查看器；需先在开发板启动主程序或独立预览')
    parser.add_argument('target', help='SSH 目标，例如 dianxin@192.168.1.100，或已配置的 SSH 主机别名')
    parser.add_argument('--local-port', type=int, default=8765)
    parser.add_argument('--remote-port', type=int, default=8765)
    args = parser.parse_args(argv)
    if args.target.startswith('-') or any(c.isspace() for c in args.target):
        parser.error('SSH 目标须为 用户名@主机 或 SSH 主机别名')
    if not 1 <= args.local_port <= 65535 or not 1 <= args.remote_port <= 65535:
        parser.error('端口须为 1~65535')
    ssh = shutil.which('ssh')
    if ssh is None:
        parser.error('未找到系统 ssh 客户端')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.local_port)) == 0:
            parser.error('本机端口已被占用，请使用 --local-port 指定其他端口')
    url = f'http://127.0.0.1:{args.local_port}/'
    command = [ssh, '-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30',
               '-L', f'127.0.0.1:{args.local_port}:127.0.0.1:{args.remote_port}', args.target]
    process = None
    try:
        print('正在连接 SSH；如提示密码或主机指纹，请在当前终端完成确认。', flush=True)
        process = subprocess.Popen(command)
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('SSH 连接失败或已退出，请查看终端提示')
            try:
                with opener.open(url + 'status', timeout=1) as response:
                    status = json.load(response)
                if status.get('service') == 'ningbo-camera-preview':
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError('未连接到预览服务，请先在开发板启动 main.py 或 camera_preview.py')
        print('已连接开发板摄像头预览：' + url, flush=True)
        try:
            if not webbrowser.open(url):
                print('请在本机浏览器手动打开以上地址。', flush=True)
        except Exception:
            print('无法自动打开浏览器，请手动打开以上地址。', flush=True)
        print('保持此终端运行；按 Ctrl+C 断开电脑预览，开发板本地预览继续。', flush=True)
        return process.wait()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print('预览连接失败：' + str(exc), flush=True)
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    raise SystemExit(main())
