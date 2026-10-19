"""安装前检查 Jetson 系统。仅读取系统信息，错误时不修改系统。"""
import json
from pathlib import Path
import platform
import re
import sys


TORCH_URL = "https://developer.download.nvidia.com/compute/redist/jp/v511/pytorch/torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl"


def inspect_platform(root=Path('/'), machine=None, python_version=None):
    errors = []
    machine = machine or platform.machine()
    python_version = python_version or sys.version_info[:2]
    model_path = root / 'proc/device-tree/model'
    release_path = root / 'etc/nv_tegra_release'
    os_path = root / 'etc/os-release'
    model = model_path.read_text(errors='replace').strip('\x00\n') if model_path.exists() else ''
    release = release_path.read_text(errors='replace') if release_path.exists() else ''
    os_info = {}
    if os_path.exists():
        for line in os_path.read_text().splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                os_info[key] = value.strip('"')
    version = re.search(r'R(\d+)\b.*?REVISION:\s*([\d.]+)', release)
    l4t = (version.group(1) + '.' + version.group(2)) if version else ''
    if machine != 'aarch64':
        errors.append('需要在 ARM64 Jetson 开发板上运行，当前架构为 ' + machine)
    if 'xavier' not in model.lower() or 'nx' not in model.lower():
        errors.append('计算模块不是可识别的 Jetson Xavier NX：' + (model or '没有设备树型号'))
    if not l4t or int(l4t.split('.')[0]) != 35 or int(l4t.split('.')[1]) < 3:
        errors.append('自动安装面向 JetPack 5.1.1 及后续 5.1.x 的 L4T R35.3+；旧系统需先使用匹配载板的 JetPack 镜像')
    if os_info.get('ID') != 'ubuntu' or os_info.get('VERSION_ID') != '20.04':
        errors.append('自动 CUDA PyTorch wheel 需要 JetPack 5 的 Ubuntu 20.04')
    if tuple(python_version) != (3, 8):
        errors.append('默认安装需使用 /usr/bin/python3 的 Python 3.8，不能使用 Conda 或其他 Python 版本')
    return {'ok': not errors, 'model': model, 'arch': machine, 'l4t': l4t,
            'ubuntu': os_info.get('VERSION_ID', ''), 'python': '.'.join(map(str, python_version)),
            'torch_url': TORCH_URL, 'errors': errors}


if __name__ == '__main__':
    result = inspect_platform()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 2)
