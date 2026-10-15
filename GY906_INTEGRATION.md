# GY-906 / MLX90614 人体测温接入

GY-906 与 BH1750 共用 Jetson I2C8：SDA 为 BOARD 3、SCL 为 BOARD 5，共地，I/O 电平为 3.3V。地址默认 0x5A；不使用 ESP32 示例的 GPIO2/15。总线读操作复用共享锁。MLX90614 要求 10—100 kHz，板上总线速率应在此范围。

`JETSON/Sensor/Config.py` 中 `body_temperature` 默认启用、型号 `mlx90614`，每秒采样。自动构建驱动，不需要从入口手工注入。读取 RAM 0x06（环境）、0x07（目标），严格验证 PEC、错误位、量程，按 `raw * 0.02 - 273.15` 转摄氏度。校验失败由设备管理器标记 error，并清除温度值；不会沿用旧温度参与 RGB 判断。

屏幕原有“人体测温”开始/停止按钮和 VP 0x1030 直接复用，温度以两位小数显示。`temperature` 为目标表面温度，`ambient_temperature` 仅用于后台状态和检查，不以环境温度替代目标温度。

显示范围默认 0—100°C，因此对着室内物体也能看到读数。RGB 继续沿用实验演示规则：35—45°C 范围内，≤37.5°C 绿色，>37.5°C 红色；范围外或数据失效为蓝色等待。这里显示的是传感器目标表面温度，没有增加体温补偿算法。

未移植 Arduino 示例的 EEPROM 发射率写入、额外按键及独立 OLED 界面。保持芯片现有发射率设置，不自动修改标定数据。

独立实测（不启动执行器）：

```bash
cd /opt/test/NingBo_Linux
.venv/bin/python -B deploy/check_mlx90614.py --samples 10
.venv/bin/python -B main.py --check-config
.venv/bin/python -B -m unittest discover -s tests -p test_mlx90614.py
```

“停止”沿用当前场景机制：停止该页刷新和 RGB 联动，并将该页数值归零；底层采样线程在程序运行期间仍继续工作。错误和超出显示范围的读数不回写数值区，状态检测页可查看采样错误。

协议依据：[Melexis MLX90614 数据手册](https://www.melexis.com/en/documents/documentation/datasheets/datasheet-mlx90614)、[Melexis SMBus 通信说明](https://www.melexis.com/en/documents/documentation/application-notes/application-note-mlx90614-smbus-communication)。
