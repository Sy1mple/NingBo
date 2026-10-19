# 当前运行工程同步记录（2026-10-19）

源码来源：`nvidia@192.168.1.106:/opt/ningbo`。209 个部署文件已按 SHA256 核对，本地正式工程为 `C:\Users\ZZH\PycharmProjects\NingBo_Minimal_NoOLED_20261016\ningbo`，本仓库存放于 `ningbo/`。完整文件校验清单见 `source_manifest_20261019.json`。

## 当前代码

- 场景实现分布在 `ningbo/experiments/`，公共硬件层为 `ningbo/hardware/`，状态检测调度位于 `ningbo/system/diagnostics/`。
- 当前配置包含风扇 A2、步进 A5、蜂鸣器 A4、舵机 A0 的反馈检测及对应回归测试；各场景屏幕地址仍由场景文件定义。
- 烟雾串口为 `/dev/ttyTHS0`、9600 8N1，校验 `FF AA H L 3C` 帧，数据超过 5 秒未更新时报异常。代码同步不代表烟雾断流已经解决。
- 开发板内核的 UART PIO 设置属于系统设备树配置；单独下载本仓库源码不会自动重现该设置。

## 本次验证

在源码目录运行 `python -m unittest discover -s tests -v`：212 项测试通过。测试使用模拟设备，不代替现场传感器和执行器实测。

本地与开发板的原有 208 个文件一致，仅补齐缺失的 `tests/test_adc_mapping.py`。本次同步未修改开发板业务代码或重启服务。

## 启动与服务

当前运行服务名为 `ningbo-new.service`，工作目录 `/opt/ningbo`，启动命令为 `/bin/bash /opt/ningbo/run.sh --port auto --camera-device auto --voice-device auto`。以目标设备实际服务配置为准；源码中的 `deploy/ningbo.service` 仍是历史部署模板，包含旧用户名和 `/opt/test/ningbo` 路径，安装前需适配。

本仓库保留根目录旧版工程以便追溯。当前版本应先 `cd ningbo` 再使用其启动、配置和测试命令。

真实 `.env.face`、`.env.voice` 凭据、运行数据库、人脸照片、下载模型、虚拟环境、缓存和日志未纳入源码同步；空白凭据模板保留。
