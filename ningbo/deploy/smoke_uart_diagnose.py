#!/usr/bin/env python3
"""Xavier NX MQ2 UART 独立诊断，不依赖项目虚拟环境。

正常运行的主服务继续持有串口。本工具读取内核计数；仅在 --probe-rx 时
短暂借用 BOARD10 为输入，观察模块 TX 到达板端的电平跳变，然后恢复 UART。
"""

import argparse
import datetime
import fcntl
import json
import mmap
import os
from pathlib import Path
import re
import struct
import subprocess
import time


UART_COUNTERS = Path("/proc/tty/driver/tegra_hsuart")
PINMUX = Path("/sys/kernel/debug/pinctrl/2430000.pinmux/pinconf-groups")
UART_PORT = "/dev/ttyTHS0"
GPIO_CHIP = "/dev/gpiochip1"
RX_LINE = 111  # Xavier NX: BOARD10 = PR.03
GPIO_BASE = 0x2212000
RX_INPUT_OFFSET = 0xE68
GPIO_CONFIG_OFFSETS = (0xE60, 0xE6C)


def counters():
    line = next((s for s in UART_COUNTERS.read_text().splitlines()
                 if s.startswith("0:") and "mmio:0x03100000" in s), None)
    if line is None:
        raise RuntimeError("找不到 ttyTHS0 的 Tegra UART 计数")
    return {key: int(value) for key, value in
            re.findall(r"\b(tx|rx|fe|brk|oe):(\d+)", line)}


def pinmux_state():
    text = PINMUX.read_text()
    match = re.search(r"^\d+ \(uart1_rx_pr3\):.*?(?=^\d+ \(|\Z)",
                      text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise RuntimeError("找不到 BOARD10 的 pinmux 配置")
    return {key: value for key, value in
            re.findall(r"^\s*([\w-]+)=([^\s]+)", match.group(), re.MULTILINE)}


def port_owners():
    result = subprocess.run(["fuser", UART_PORT], capture_output=True,
                            text=True, timeout=3, check=False)
    return [int(pid) for pid in result.stdout.split() if pid.isdigit()]


def probe_rx(seconds):
    """只读采样 BOARD10；无论采样是否成功都关闭 GPIO 句柄并恢复寄存器。"""
    state = pinmux_state()
    if state.get("func") != "uarta" or state.get("gpio-mode") != "1":
        raise RuntimeError("BOARD10 当前不在 UART 模式，拒绝临时切换")
    mem_fd = chip_fd = line_fd = region = None
    saved = {}
    try:
        mem_fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        region = mmap.mmap(mem_fd, 0x1000, offset=GPIO_BASE)
        saved = {offset: struct.unpack_from("<I", region, offset)[0]
                 for offset in GPIO_CONFIG_OFFSETS}
        if saved[GPIO_CONFIG_OFFSETS[0]] != 0:
            raise RuntimeError("BOARD10 的 GPIO 配置已被其他功能启用，拒绝借用")
        chip_fd = os.open(GPIO_CHIP, os.O_RDONLY)
        info = bytearray(72)
        struct.pack_into("<I", info, 0, RX_LINE)
        fcntl.ioctl(chip_fd, 0xC048B402, info, True)  # GPIO_GET_LINEINFO_IOCTL
        # struct gpioline_info: offset(4), flags(4), name(32), consumer(32)
        name = bytes(info[8:40]).split(b"\0", 1)[0].decode("ascii", "replace")
        if name != "PR.03":
            raise RuntimeError(f"GPIO line {RX_LINE} 是 {name}，不是 BOARD10/PR.03")
        request = bytearray(364)
        struct.pack_into("<I", request, 0, RX_LINE)
        struct.pack_into("<I", request, 256, 1)  # GPIOHANDLE_REQUEST_INPUT
        label = b"ningbo-smoke-diagnose"
        request[324:324 + len(label)] = label
        struct.pack_into("<I", request, 356, 1)
        fcntl.ioctl(chip_fd, 0xC16CB403, request, True)
        line_fd = struct.unpack_from("<i", request, 360)[0]
        deadline = time.monotonic() + seconds
        high = low = transitions = 0
        previous = None
        while time.monotonic() < deadline:
            level = struct.unpack_from("<I", region, RX_INPUT_OFFSET)[0] & 1
            high += level
            low += 1 - level
            if previous is not None and level != previous:
                transitions += 1
            previous = level
        return {"seconds": seconds, "high_samples": high, "low_samples": low,
                "transitions": transitions, "last_level": previous}
    finally:
        if line_fd is not None:
            os.close(line_fd)  # kernel gpio_free 恢复 UART 复用
        if chip_fd is not None:
            os.close(chip_fd)
        if region is not None:
            for offset, value in saved.items():
                struct.pack_into("<I", region, offset, value)
            region.close()  # /dev/mem 映射不支持 mmap.flush()
        if mem_fd is not None:
            os.close(mem_fd)


def reconnect_port():
    fd = os.open(UART_PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, 0x5437, 0)  # TIOCVHANGUP；主服务自行重开串口
    finally:
        os.close(fd)


def diagnose(seconds, probe, reconnect):
    result = {"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "port": UART_PORT, "owners": port_owners(),
              "pinmux_before": pinmux_state(), "before": counters()}
    time.sleep(seconds)
    result["after"] = counters()
    result["rx_delta"] = result["after"].get("rx", 0) - result["before"].get("rx", 0)
    if result["rx_delta"] <= 0 and probe:
        result["rx_pad"] = probe_rx(min(2.0, seconds))
        result["pinmux_after"] = pinmux_state()
    if result["rx_delta"] <= 0 and reconnect:
        result["before_reconnect"] = counters()
        reconnect_port()
        time.sleep(seconds)
        result["after_reconnect"] = counters()
        result["reconnect_rx_delta"] = (result["after_reconnect"].get("rx", 0)
                                        - result["before_reconnect"].get("rx", 0))
    if result["rx_delta"] > 0 or result.get("reconnect_rx_delta", 0) > 0:
        result["finding"] = "UART 正在收到字节；若状态仍异常，再查帧格式或屏幕结果"
    elif result.get("rx_pad", {}).get("transitions", 0) == 0 and probe:
        result["finding"] = "BOARD10 没有串口跳变；检查模块 TXD、插头及中间信号路径"
    elif probe:
        result["finding"] = "BOARD10 有跳变但 UART 没有收字节；检查复用、电平和 UART 驱动"
    else:
        result["finding"] = "UART 没有收字节；加 --probe-rx 可区分接收脚是否有信号"
    return result


def main():
    parser = argparse.ArgumentParser(description="独立检查 MQ2 到 Jetson 的 UART 链路")
    parser.add_argument("--seconds", type=float, default=5.0,
                        help="每次观察的秒数，默认 5 秒")
    parser.add_argument("--probe-rx", action="store_true",
                        help="无收帧时，临时把 BOARD10 切为输入采样，需 sudo")
    parser.add_argument("--reconnect", action="store_true",
                        help="无收帧时，仅尝试一次 UART 重连，需 sudo")
    parser.add_argument("--output", type=Path, help="可选 JSON 结果文件")
    args = parser.parse_args()
    if not 0.5 <= args.seconds <= 30:
        parser.error("--seconds 必须在 0.5～30 之间")
    result = diagnose(args.seconds, args.probe_rx, args.reconnect)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()
