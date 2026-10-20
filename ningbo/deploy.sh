#!/usr/bin/env bash
# Jetson Xavier NX / JetPack 5.1.x：从项目目录执行 bash deploy.sh。
set -Eeuo pipefail
export PYTHONNOUSERSITE=1
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON=/usr/bin/python3
VENV="$ROOT/.venv"
CHECK_ONLY=0
PLAN_ONLY=0
TORCH_WHEEL=""
PHASE="参数检查"

usage() {
  cat <<'USAGE'
用法：bash deploy.sh [--plan | --check-only] [--torch-wheel /path/to/file.whl]
  无参数       安装系统库、虚拟环境及所选后端依赖，配置权限并检查
  --plan       只检查平台和模型，显示安装步骤；不安装、不改权限
  --check-only 只检查已部署环境和实际模型前向；不安装
  --torch-wheel 指定已下载的官方 cp38 linux_aarch64 CUDA PyTorch wheel
目标：Jetson Xavier NX，JetPack 5.1.1+ / L4T R35.3+，Ubuntu 20.04，系统 Python 3.8。
脚本需要联网和 sudo；仅启用本地后端时需要补齐模型。云端 API 须另行配置密钥和免费授权。
USAGE
}
while (($#)); do
  case "$1" in
    --plan) PLAN_ONLY=1; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --torch-wheel) [[ $# -ge 2 ]] || { usage; exit 2; }; TORCH_WHEEL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 2 ;;
  esac
done
die() { echo "部署未完成：$*" >&2; exit 1; }
trap 'code=$?; printf "\n步骤失败：%s，退出码 %s。请查看部署日志；尚未标记部署成功。\n" "$PHASE" "$code" >&2; exit "$code"' ERR
[[ "$(uname -s)" == Linux ]] || die '请把部署包复制到 Jetson，再在 Linux 终端运行本脚本。'
[[ -x "$PYTHON" ]] || die '系统 /usr/bin/python3 不存在，请先确认 JetPack 安装完整。'
PHASE="平台兼容检查"
"$PYTHON" "$ROOT/deploy/platform_check.py"
PHASE="模型完整性检查"
cd "$ROOT"
"$PYTHON" "$ROOT/deploy/verify_models.py" "$ROOT"
FACE_BACKEND="$("$PYTHON" -B -c 'from config import DEFAULT_CONFIG; print(DEFAULT_CONFIG.face.backend)')"
VOICE_BACKEND="$("$PYTHON" -B -c 'from config import DEFAULT_CONFIG; print(DEFAULT_CONFIG.voice.backend)')"
if ((PLAN_ONLY)); then
  printf '%s\n' "安装计划：系统依赖 → 项目 .venv → 所选后端依赖（人脸=$FACE_BACKEND，语音=$VOICE_BACKEND）→ 设备权限 → 检查。云端模式不安装 CUDA PyTorch/Whisper。"
  exit 0
fi
if ((CHECK_ONLY)); then
  [[ -x "$VENV/bin/python" ]] || die '尚无 .venv，请先执行 bash deploy.sh。'
  cd "$ROOT"
  "$VENV/bin/python" -B main.py --check-config
  "$VENV/bin/python" -B main.py --check-acceleration
  exit 0
fi
if ((EUID == 0)); then
  if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
    ARGS=()
    [[ -z "$TORCH_WHEEL" ]] || ARGS+=(--torch-wheel "$TORCH_WHEEL")
    exec sudo -H -u "$SUDO_USER" -- bash "$ROOT/deploy.sh" "${ARGS[@]}"
  fi
  die '请使用普通 Jetson 用户执行 bash deploy.sh，脚本会在需要时调用 sudo。'
fi
command -v sudo >/dev/null || die '需要 sudo 安装系统库。'
RUN_USER="$(id -un)"
mkdir -p "$ROOT/logs" "$ROOT/.deploy-cache"
LOG="$ROOT/logs/deploy_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
echo "日志：$LOG"
PHASE="获取系统安装权限"
sudo -v
PHASE="安装 Ubuntu 与 NVIDIA JetPack 组件"
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3-venv python3-pip python3-dev build-essential pkg-config ca-certificates curl unzip \
  libopenblas-dev libportaudio2 portaudio19-dev libsndfile1 ffmpeg alsa-utils v4l-utils \
  i2c-tools libgomp1 libjpeg-dev zlib1g-dev libgpiod2 libgpiod-dev libcap2-bin
# 这些组件从板上已有的 JetPack apt 软件源安装，不能换成桌面 NVIDIA 驱动。
if [[ "$FACE_BACKEND" == tensorrt || "$VOICE_BACKEND" == whisper_cuda ]]; then
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-cuda nvidia-cudnn8 cuda-nvtx-11-4
fi
if [[ "$FACE_BACKEND" == tensorrt ]]; then
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-tensorrt python3-libnvinfer
fi
sudo ldconfig
export LD_LIBRARY_PATH="/usr/local/cuda/lib64:/usr/local/cuda/targets/aarch64-linux/lib:${LD_LIBRARY_PATH:-}"
PHASE="创建独立项目 Python 环境"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/python" -c 'import sys; assert sys.version_info[:2] == (3,8), "现有 .venv 不是 Python 3.8，请保留后另建正确环境"'
"$VENV/bin/python" -m pip install --upgrade 'pip<25.1' 'setuptools<70' 'wheel<0.46'
# 系统包只读共享 TensorRT；项目依赖实际装入 .venv，不卸载系统环境。
"$VENV/bin/python" -m pip install --ignore-installed -r "$ROOT/requirements.txt" -c "$ROOT/constraints-jetson.txt"
if [[ "$FACE_BACKEND" == tensorrt ]]; then
  "$VENV/bin/python" -c 'import tensorrt as trt; assert trt.__version__.split(".")[0] == "8", trt.__version__; print("TensorRT", trt.__version__)'
fi
if [[ "$VOICE_BACKEND" == whisper_cuda ]]; then
PHASE="安装或复用 CUDA PyTorch"
if "$VENV/bin/python" -c 'import torch; assert torch.cuda.is_available(); a=torch.ones(8,device="cuda"); assert float((a*a).sum())==8; print(torch.__version__,torch.version.cuda)' ; then
  echo '现有 CUDA PyTorch 已通过实际 GPU 运算检查，保留使用。'
else
  if [[ -n "$TORCH_WHEEL" ]]; then
    [[ -f "$TORCH_WHEEL" && "$TORCH_WHEEL" == *cp38*linux_aarch64.whl ]] || die '指定 wheel 必须是官方 cp38 linux_aarch64 PyTorch 文件。'
    WHEEL="$TORCH_WHEEL"
  else
    WHEEL="$ROOT/.deploy-cache/torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl"
    URL='https://developer.download.nvidia.com/compute/redist/jp/v511/pytorch/torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl'
    if [[ ! -f "$WHEEL" ]]; then
      curl --fail --location --retry 3 --output "$WHEEL.part" "$URL"
      mv -- "$WHEEL.part" "$WHEEL"
    fi
  fi
  # --ignore-installed 只在 venv 安装，避免卸载 --system-site-packages 共享的系统 torch。
  "$VENV/bin/python" -m pip install 'typing_extensions>=4.4,<4.14' 'sympy<1.14' 'networkx<3.2' 'jinja2<3.2' 'filelock<3.17'
  "$VENV/bin/python" -m pip install --ignore-installed --no-deps "$WHEEL"
  "$VENV/bin/python" -c 'import torch; assert torch.cuda.is_available(), "CUDA PyTorch 无法使用 GPU"; a=torch.ones(8,device="cuda"); assert float((a*a).sum())==8; print(torch.__version__,torch.version.cuda)'
fi
PHASE="安装 CUDA Whisper 依赖"
# 官方提供 cp38 ARM64 wheel，直接安装二进制，避免现场编译 LLVM。
"$VENV/bin/python" -m pip install --only-binary=:all: 'llvmlite==0.41.1' -c "$ROOT/constraints-jetson.txt"
"$VENV/bin/python" -m pip install -r "$ROOT/requirements-whisper.txt" -c "$ROOT/constraints-jetson.txt"
"$VENV/bin/python" -m pip install 'openai-whisper==20231117' --no-deps
fi
PHASE="配置 GPIO 串口 I2C 摄像头与音频权限"
for group in gpio i2c dialout video audio; do
  getent group "$group" >/dev/null || sudo groupadd --system "$group"
done
sudo usermod -a -G gpio,i2c,dialout,video,audio "$RUN_USER"
GPIO_RULES="$("$VENV/bin/python" - <<'PY'
from pathlib import Path
import importlib.util
spec = importlib.util.find_spec('Jetson')
path = Path(next(iter(spec.submodule_search_locations))) / 'GPIO' / '99-gpio.rules'
if not path.is_file():
    raise SystemExit('Jetson.GPIO 官方 99-gpio.rules 未找到')
print(path)
PY
)"
sudo install -m 0644 "$GPIO_RULES" /etc/udev/rules.d/99-gpio.rules
sudo install -m 0644 "$ROOT/deploy/99-ningbo-i2c.rules" /etc/udev/rules.d/99-ningbo-i2c.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=gpio
sudo udevadm trigger --subsystem-match=i2c-dev
PHASE="配置烟雾专用 UART"
bash "$ROOT/deploy/configure_smoke_uart.sh"
PHASE="编译 DHT11 原生 GPIO 采样器"
gcc -O2 -Wall -Wextra -std=c11 "$ROOT/experiments/environment/jetson/native/dht11_gpiod.c" -lgpiod \
  -o "$ROOT/experiments/environment/jetson/native/dht11_gpiod.next"
sudo install -o root -g root -m 0755 "$ROOT/experiments/environment/jetson/native/dht11_gpiod.next" \
  "$ROOT/experiments/environment/jetson/native/dht11_gpiod"
rm -- "$ROOT/experiments/environment/jetson/native/dht11_gpiod.next"
sudo setcap cap_sys_nice,cap_sys_rawio,cap_dac_read_search=ep "$ROOT/experiments/environment/jetson/native/dht11_gpiod"
PHASE="工程检查与模型实际 GPU 推理"
cd "$ROOT"
"$VENV/bin/python" -B main.py --check-config
"$VENV/bin/python" -B main.py --init-face-db
"$VENV/bin/python" -B main.py --check-acceleration
"$VENV/bin/python" -m pip freeze > "$ROOT/logs/requirements_installed.txt"
printf '%s\n' '部署完成：Python 依赖和当前后端检查通过。云端 API 的授权、免费额度及识别效果尚须实测。' \
  '设备用户组变更需要重新登录终端一次。接线与载板 pinmux 确认后，执行 bash run.sh 启动。' \
  '不自动启动电机、风扇或门锁；脚本的验证不打开摄像头、麦克风、GPIO 或串口。'
