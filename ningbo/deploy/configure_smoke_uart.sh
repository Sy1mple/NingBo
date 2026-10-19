#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
if ! "$ROOT/.venv/bin/python" -B -c 'import sys; from config import DEFAULT_CONFIG as c; sys.exit(0 if c.smoke.enabled and c.smoke.port == "/dev/ttyTHS0" else 1)'; then
  echo '烟雾配置未使用 ttyTHS0，跳过物理 8/10 脚 UART 配置。'
  exit 0
fi
sudo install -m 0644 "$ROOT/deploy/99-ningbo-uart.rules" /etc/udev/rules.d/99-ningbo-uart.rules
sudo systemctl disable --now nvgetty.service
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --sysname-match=ttyTHS0
sudo chgrp dialout /dev/ttyTHS0
sudo chmod 0660 /dev/ttyTHS0
echo 'BOARD 8=TX、10=RX：烟雾使用 ttyTHS0；串口登录已关闭，dialout 权限已配置。'
