# ADS1115 状态检测接入

Jetson I2C8 / 0x48，默认量程 ±4.096V，单端 A0～A3 顺序读取。
ESP32 示例仅参考采集流程，未烧录 ESP32。

项目 `DeviceService.read_adc()` 读取四路；`check("ads1115")` 或现有 `check("current")` 进入状态检测。
当电流通道未确认时，报告包含 adc_status、readings 和 check_scope=adc_voltage，设备状态保持 unconfigured。
不会将接近零伏解释为设备正常，也不会自动使用未确认的分流电阻换算。
已确认电流通道启用后，电流驱动同样使用 OS 轮询、配置读回和超时检查。

实测：

```bash
cd /opt/test/NingBo_Linux
.venv/bin/python deploy/check_ads1115.py --samples 10
```

必须核对 A0～A3 连接、ADC 实际型号、供电、共地、信号范围，以及分流电阻/分压比例，才能标定设备状态。
量程是换算范围，不是输入耐压。先用 GND 和已知安全电压校准。
