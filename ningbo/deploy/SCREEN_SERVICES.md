# 独立串口屏服务（2026-10-19）

正式工程使用两个独立业务进程，两者之间没有 Requires、PartOf、BindsTo 或相互重启关系：

| 服务 | 职责 | 物理设备 |
| --- | --- | --- |
| ningbo-new.service | 传感器采样、状态检测、执行器、语音和人脸业务 | ttyTHS0、GPIO、I2C、音视频设备 |
| ningbo-screen.service | 串口屏自动发现、收发、断线重连 | USB 串口屏 |

设备进程不再实例化物理 ScreenService，只使用 RemoteScreenService。屏幕进程不实例化 DeviceService，也不打开烟雾 UART。屏幕服务额外使用 systemd 设备访问策略，只允许 USB 串口，禁止板载 ttyTHS0。

两者通过 `/run/ningbo-screen/bridge.sock` 通信，目录权限 0700、socket 权限 0600，仅相同运行用户可访问。设备服务可在屏幕服务缺席时启动，持续采样和执行已经开始的业务。重连后重新发送最新 VP 显示值；不保存离线按钮，超过 2 秒的 IPC 按钮事件也会丢弃。显示缓存限制 4096 Word，收发缓冲各限制 256 KiB。设备日志中的“提交写屏”表示交给显示缓存，不代表屏幕硬件已确认。

## 当前开发板安装

模板针对当前设备的 `nvidia` 用户和 `/opt/ningbo` 路径。其他设备需先修改两个 unit 的 User、Group、WorkingDirectory 和 ExecStart。两个服务必须使用同一用户。

```bash
sudo install -m 644 deploy/ningbo-new.service /etc/systemd/system/ningbo-new.service
sudo install -m 644 deploy/ningbo-screen.service /etc/systemd/system/ningbo-screen.service
sudo install -m 644 deploy/ningbo-screen-modules.service /etc/systemd/system/ningbo-screen-modules.service
sudo systemctl daemon-reload
sudo systemctl enable ningbo-new.service ningbo-screen.service
sudo systemctl restart ningbo-new.service ningbo-screen.service
```

已安装的旧单进程服务仅在首次迁移时需要重启一次。以后只重启屏幕：

```bash
sudo systemctl restart ningbo-screen.service
```

该操作应保持设备服务 MainPID、烟雾 ttyTHS0 文件描述符不变；停止屏幕服务时烟雾 RX 计数仍应持续增加。重新烧录屏幕后可重启屏幕服务以重新探测。软件进程隔离不能隔离外部共用供电、共地或信号电平干扰，也不证明既有烟雾断流的根因已经解决。

## 手动运行

### 开机 USB 串口权限

屏幕服务启动前通过 `ningbo-screen-modules.service` 加载 `usbserial` 和 `cdc_acm`，注册 `ttyUSB` 和 `ttyACM` 设备组。此辅助单元由屏幕服务自动拉起，无需单独 enable。`DeviceAllow=char-ttyUSB/char-ttyACM` 只解析启动时已注册的设备组；若驱动晚于屏幕服务加载，服务虽然 active，却会持续报打开串口 `Operation not permitted`，手动重启才恢复。辅助单元保证先注册设备组，再创建屏幕服务的设备访问策略，继续禁止访问板载烟雾 UART。

安装后可检查 `/sys/fs/cgroup/devices/system.slice/ningbo-screen.service/devices.list`（当前设备使用 cgroup v1），应包含 USB 串口设备组许可；本机为 `c 188:* rw` 和 `c 166:* rw`，不应包含烟雾 `ttyTHS` 设备组。完成修复后需另行断电上电验证启动顺序。

### 终端运行

Linux 中在一个终端运行屏幕进程，另一个运行设备进程。socket 所在目录应由运行用户拥有。

```bash
mkdir -p /tmp/ningbo-screen-local
chmod 700 /tmp/ningbo-screen-local
export NINGBO_SCREEN_SOCKET=/tmp/ningbo-screen-local/bridge.sock
.venv/bin/python -B screen_daemon.py --port auto
# 另一个终端设置同一个 NINGBO_SCREEN_SOCKET，然后：
bash run.sh --camera-device auto --voice-device auto
```

屏幕的 `--port` 和 `--baudrate` 参数改由 screen_daemon.py 接收；设备 main.py 不再接受这两个运行覆盖参数。旧 deploy/ningbo.service 为历史模板，不要用于本次部署。
