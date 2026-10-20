# MQ2 串口单独诊断

开发板上执行：

```bash
cd /opt/ningbo
sudo python3 deploy/smoke_uart_diagnose.py --seconds 5 --probe-rx --reconnect \
  --output logs/smoke-diagnosis.json
```

主服务可以保持运行。脚本先比较 `/dev/ttyTHS0` 的内核接收计数。没有新字节时，`--probe-rx` 临时把 BOARD10 设为**输入**，观察模块 TX 信号是否到达排针，然后恢复 UART 复用；`--reconnect` 最多尝试一次 UART 断开重连。两个可选步骤都不会给模块断电。

- `rx_delta > 0`：开发板收到字节。如果串口屏仍显示异常，接着查帧格式、采样状态和写屏结果。
- `rx_delta = 0`、`rx_pad.transitions = 0`：开发板接收脚没有数据跳变。检查模块 TXD、插头及 PCB 信号路径。
- `rx_delta = 0`、`rx_pad.transitions > 0`：信号到达接收脚但内核没有收到字节。检查 UART 复用、电平和驱动。
- `reconnect_rx_delta > 0`：尝试重连后开始收到字节；否则表示软件重连没有恢复。

脚本要求 Xavier NX 上的 BOARD10 对应 PR.03、`/dev/gpiochip1` 第 111 线。如果板型或引脚布局改变，它会拒绝临时切换 GPIO。**检测“正常”仍以有效 `FF AA H L 3C` 帧为准，收到任意字节不等于传感器正常。**
