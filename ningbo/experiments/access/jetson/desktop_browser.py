"""在当前用户的本地 Linux 桌面打开火狐，SSH 无桌面时不弹出错误窗口。"""
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys

LOGGER = logging.getLogger(__name__)
GUI_KEYS = ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS')


def local_desktop_environment(environ=None, run=subprocess.run):
    """只查当前用户桌面环境，不连接别人的桌面，也不使用 SSH X11 转发显示。"""
    source = dict(os.environ if environ is None else environ)
    if not sys.platform.startswith('linux'):
        return None
    gui = {k: source[k] for k in GUI_KEYS if source.get(k)}
    display = gui.get('DISPLAY', '')
    if not (display.startswith(':') or gui.get('WAYLAND_DISPLAY')):
        gui = {}  # SSH 的 XAUTHORITY 也不能误用到本地桌面。
        # systemd 自启/SSH 启动时，图形会话变量通常没有继承。
        try:
            result = run(['systemctl', '--user', 'show-environment'], capture_output=True,
                         text=True, timeout=2, check=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    key, sep, value = line.partition('=')
                    if sep and key in GUI_KEYS and value:
                        gui[key] = value
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not gui.get('DISPLAY', '').startswith(':') and not gui.get('WAYLAND_DISPLAY'):
        # 某些桌面未把 DISPLAY 导入 user manager；只查询同 UID 的本地活动会话。
        try:
            sessions = run(['loginctl', 'list-sessions', '--no-legend', '--no-pager'],
                           capture_output=True, text=True, timeout=2, check=False)
            uid = str(os.getuid())
            for line in sessions.stdout.splitlines():
                fields = line.split()
                if len(fields) < 2 or fields[1] != uid:
                    continue
                result = run(['loginctl', 'show-session', fields[0], '-p', 'Active', '-p', 'Remote',
                              '-p', 'Type', '-p', 'Display'], capture_output=True, text=True, timeout=2, check=False)
                details = dict(item.split('=', 1) for item in result.stdout.splitlines() if '=' in item)
                if details.get('Active') != 'yes' or details.get('Remote') != 'no':
                    continue
                if details.get('Type') == 'x11' and details.get('Display', '').startswith(':'):
                    gui['DISPLAY'] = details['Display']
                    break
                if details.get('Type') == 'wayland':
                    runtime = Path('/run/user') / uid
                    sockets = sorted(p for p in runtime.glob('wayland-*') if p.is_socket())
                    if sockets:
                        gui['WAYLAND_DISPLAY'] = sockets[0].name
                        break
        except (OSError, subprocess.TimeoutExpired):
            pass
    display = gui.get('DISPLAY', '')
    if not display.startswith(':'):
        gui.pop('DISPLAY', None)
    if not gui.get('DISPLAY') and not gui.get('WAYLAND_DISPLAY'):
        return None
    # 去除 SSH 继承的 X11 变量，再填当前用户实际桌面的值。
    env = {k: v for k, v in source.items() if k not in GUI_KEYS}
    env.update(gui)
    if not env.get('XDG_RUNTIME_DIR') and hasattr(os, 'getuid'):
        runtime = Path('/run/user') / str(os.getuid())
        if runtime.is_dir():
            env['XDG_RUNTIME_DIR'] = str(runtime)
    if not env.get('XAUTHORITY'):
        authority = Path(env.get('XDG_RUNTIME_DIR', '/nonexistent')) / 'gdm' / 'Xauthority'
        if not authority.is_file():
            authority = Path.home() / '.Xauthority'
        if authority.is_file():
            env['XAUTHORITY'] = str(authority)
    if not env.get('DBUS_SESSION_BUS_ADDRESS') and env.get('XDG_RUNTIME_DIR'):
        bus = Path(env['XDG_RUNTIME_DIR']) / 'bus'
        if bus.is_socket():
            env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + str(bus)
    return env


def launch_local_firefox(url, environ=None, run=subprocess.run, popen=subprocess.Popen, which=shutil.which):
    env = local_desktop_environment(environ, run)
    if env is None:
        return None  # 尚无图形桌面，调用方稍后重试。
    executable = which('firefox')
    if executable is None:
        LOGGER.warning('未找到 Firefox；可在开发板浏览器手动打开 %s', url)
        return False
    try:
        process = popen([executable, '--new-window', url], env=env, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        # 已有 Firefox 进程接受命令时启动器会迅速以 0 退出，属于正常情况。
        try:
            code = process.wait(timeout=0.8)
            if code != 0:
                LOGGER.warning('Firefox 启动失败（退出码 %s），可手动打开 %s', code, url)
                return None
        except subprocess.TimeoutExpired:
            pass
        LOGGER.info('已向本地桌面发送 Firefox 打开请求：%s', url)
        return True
    except OSError:
        LOGGER.exception('无法启动本地 Firefox；预览服务继续运行')
        return None
