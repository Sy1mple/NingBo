# 当前运行工程同步记录（2026-10-19）

源码来源：`nvidia@192.168.1.106:/opt/ningbo`。215 个部署文件已按 SHA256 核对，本地正式工程为 `C:\Users\ZZH\PycharmProjects\NingBo_Minimal_NoOLED_20261016\ningbo`，本仓库存放于 `ningbo/`。完整文件校验清单见 `source_manifest_20261019.json`。

## 当前代码

- 场景实现分布在 `ningbo/experiments/`，公共硬件层为 `ningbo/hardware/`，状态检测调度位于 `ningbo/system/diagnostics/`。
- 当前配置包含风扇 A2、步进 A5、蜂鸣器 A4、舵机 A0 的反馈检测及对应回归测试；各场景屏幕地址仍由场景文件定义。
- 烟雾串口为 `/dev/ttyTHS0`、9600 8N1，校验 `FF AA H L 3C` 帧，数据超过 5 秒未更新时报异常。代码同步不代表烟雾断流已经解决。
- 开发板内核的 UART PIO 设置属于系统设备树配置；单独下载本仓库源码不会自动重现该设置。

## 本次验证

在源码目录运行 `python -m unittest discover -s tests -v`：221 项测试通过（Linux 全部执行；Windows 跳过 9 项 Unix socket 测试）。测试使用模拟设备，不代替现场传感器和执行器实测。

本地与开发板的原有 208 个文件一致，仅补齐缺失的 `tests/test_adc_mapping.py`。随后完成串口屏进程隔离并部署，迁移时重启一次设备服务；迁移后屏幕服务独立启停。

## 启动与服务

设备业务服务为 `ningbo-new.service`，工作目录 `/opt/ningbo`，启动命令为 `/bin/bash /opt/ningbo/run.sh --camera-device auto --voice-device auto`。串口屏由独立 `ningbo-screen.service` 运行 `screen_daemon.py --port auto`，通过受权限保护的本地 Unix socket 交换显示和按钮消息。以目标设备实际服务配置为准；源码中的 `deploy/ningbo.service` 仍是历史部署模板，包含旧用户名和 `/opt/test/ningbo` 路径，安装前需适配。

本仓库保留根目录旧版工程以便追溯。当前版本应先 `cd ningbo` 再使用其启动、配置和测试命令。

真实 `.env.face`、`.env.voice` 凭据、运行数据库、人脸照片、下载模型、虚拟环境、缓存和日志未纳入源码同步；空白凭据模板保留。


## 串口屏进程隔离验证

- Linux 回归：221 项通过，包含真实 Unix socket 的断连恢复、屏幕读写失败、离线启动、显示重放和按钮过期丢弃。
- 正式部署后，停止屏幕服务及多次独立重启屏幕服务，设备进程 MainPID 保持 148376，烟雾连接始终为 fd 17；没有联动重启。
- 实机端口归属：设备进程仅打开 ttyTHS0，屏幕进程仅打开 ttyUSB0。串口屏进程的 systemd DevicePolicy=closed，只允许 USB 串口设备。
- 实机点击已通过 IPC 触发传感器检测和烟雾开始/停止，结果提交给独立屏幕服务。
- 初次验证时烟雾 RX 停在 82219；用户随后确认有一根插头线未插实，重新接好后有效帧恢复，实屏状态正常。最终测试中停止屏幕约 12 秒并独立重启屏幕三次，RX 从 83779 增至 84074，设备 PID 148376 和烟雾 fd 17 全程不变；帧错误及 BREAK 计数没有增加。隔离实机验证通过，但尚未进行解耦后的屏幕工程重新烧录复测，不能断言历史烟雾断流全部由共用进程引起。
- 完整日志保留于开发板 `/opt/ningbo/logs/screen-decouple-*.json`；迁移前源码和 unit 备份位于 `/opt/ningbo/backups/screen-decouple-20261019_082226`。
