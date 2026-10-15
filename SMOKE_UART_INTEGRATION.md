# 烟雾 UART 接入

## 接线及端口

- Jetson 物理 8 脚是 TX，接传感器 RXD。
- Jetson 物理 10 脚是 RX，接传感器 TXD；双方 GND 共地。
- 设备路径 `/dev/ttyTHS0`，9600、8N1。原 `/dev/ttyTHS1` 配置已修正。
- `nvgetty.service` 会占用该 UART，部署脚本关闭此串口登录并设置 dialout 用户组权限。SSH 继续使用网络，不受影响。
- 持久化权限规则为 `deploy/99-ningbo-uart.rules`，可单独运行 `bash deploy/configure_smoke_uart.sh` 安装。

## 数据及显示

依照用户提供的 `smoke_uart_test.ino`：`FF AA H L 3C`，浓度为 `(H << 8) | L`。例如 `FF AA 02 58 3C` 返回 600。该协议没有校验和；驱动验证帧头、固定长度和帧尾，支持分包、粘包、噪声、重同步，解析残留缓冲不超过 4 字节。

默认 `smoke.protocol="ffaa"`。驱动结果包括 concentration、unit=raw、原始字节、最近完整帧、有效帧/尾错误/丢弃字节计数及真实测量时间。不要把原始浓度当成百分比或声称为 ppm；具体单位需传感器型号或手册确认。

屏幕沿用原烟雾开始/停止按钮及 VP 0x1050，显示原始浓度，不把 600 压为 100。如果屏幕原控件标了百分号，应在屏幕工程中改成“原始浓度”或型号确认后的实际单位。屏幕当前代码按有符号 16 位 Word 显示，超过 32767 时保留后台完整值、拒绝该数值写屏并记录警告，避免变成负数或静默截断。

有效帧最多保留 5 秒，等待分包或短暂无数据不刷新测量时间；超过时限清除浓度并报告 error。收到字节不等于测量正常，收到完整帧且未过期才报告 normal。无效读数不伪造为零或参与正常判断；数值显示不更新，状态检测页可检查异常。串口断开后关闭连接，下次采样重连，丢弃旧残帧及旧测量值。

## 检查

```bash
cd /opt/test/NingBo_Linux
.venv/bin/python -B -m unittest discover -s tests -p test_smoke_uart.py
.venv/bin/python -B main.py --check-config
# 独立实测前停止主程序，避免两进程竞争 UART；测试结束恢复服务。
sudo systemctl stop ningbo.service
.venv/bin/python -B deploy/check_smoke_uart.py --samples 10
sudo systemctl start ningbo.service
```

原 raw 模式和显式注入的百分比 decoder 接口保留。没有新增未经标定的烟雾报警阈值。

物理 UART 映射参考：[NVIDIA 开发者论坛的 Xavier NX UART 说明](https://forums.developer.nvidia.com/t/creating-symlinks-for-uart-in-jetson-nano-and-xavier-nx/176289)。

## MQ2 间歇无数据：保留故障现场（2026-10-15）

- 再次出现烟雾数值不显示或不刷新时，先不要给 MQ2 断电、拔线或重启程序/开发板，保留现场后检查。
- 先记录操作时间和此前操作过的场景，再检查 `ningbo` 服务、`smoke.start/stop` 日志，以及 `/proc/tty/driver/tegra_hsuart` 的 RX 计数是否持续增长。串口能打开不代表正在收到数据；启动日志中的旧测量值也不能证明当前仍有数据。
- 分别核对当前有效烟雾帧、采样时间、写屏值和屏幕 VP `0x1050` 读回值，区分传感器停发与显示异常。不要将过期数据或无数据伪装成正常测量值。
- 本轮实测曾出现 0 字节/接收计数不再增长；MQ2 单独重新上电后恢复约每 0.5 秒一个 `FF AA H L 3C` 帧。显示联调已核对写入和读回值一致。停发的具体原因尚未确定，不能直接认定模块卡死、供电故障、串口变号或程序错误。
- 接法沿用物理 8=TX、10=RX、双方 GND 共地，接口 `/dev/ttyTHS0`，9600、8N1。排针上的串口通过此设备接口访问，不是 USB 串口。
- 独立采集前停止正式服务，结束后释放串口并恢复 `ningbo`；服务重启会清空场景启动状态，需重新点击对应的“开始检测”。现场记录保存在 `artifacts/jetson_smoke_20261015/`。
