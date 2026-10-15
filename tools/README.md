# 硬件测试与调参入口

命令从项目根目录执行。自动测试不操作实物；硬件诊断工具需要在 Jetson 上运行。下列工具沿用原来的 `deploy/` 路径，独立实验和旧补丁已另行归档。

## 当前诊断工具

| 任务 | 命令或入口 | 行为 |
| --- | --- | --- |
| ADS1115 四通道采样 | `.venv/bin/python deploy/check_ads1115.py --address 0x48 --samples 5` | 只读 ADC，第二块板可使用 `0x49` |
| GY-906 / MLX90614 体温采样 | `.venv/bin/python deploy/check_mlx90614.py --samples 5` | 只读温度，不写 EEPROM |
| 烟雾 UART 收帧 | `.venv/bin/python deploy/check_smoke_uart.py --samples 10` | 只读烟雾串口 |
| 舵机 ADC 关联实测 | `.venv/bin/python deploy/check_servo_adc.py` | 舵机依次到 45、90、135、90 度并采样 A0 |
| RGB ADC 关联实测 | `.venv/bin/python deploy/check_rgb_adc.py` | 依次切换灯色并采样 A3 |
| 步进持续正转测试 | `.venv/bin/python deploy/run_stepper_forward_test.py` | 启动即转动，持续补充步数；屏幕停止键或 Ctrl+C 可停 |
| 模型文件完整性 | `python deploy/verify_models.py` | 校验随项目保存的模型 |
| 无硬件回归测试 | `python -m unittest discover -s tests -v` | 使用测试替身，不启动实物 |


独立硬件测试应先停止持有相同 GPIO、I2C 或串口的主服务：`sudo systemctl stop ningbo`。测试结束退出脚本后，再运行 `sudo systemctl start ningbo`。这些命令仅供手动测试，本次同步没有执行。

舵机与 RGB 旧测试工具仍把结果写到 `/home/dianxin/usb-hub-test/`；该目录已存在于当前设备。步进持续正转测试写入 `logs/stepper_forward_test.json`，运行前需确保 `logs/` 存在。它们都是单独的实机调试入口，不能与主程序同时启动。

## 屏幕按钮的常规步进测试

使用正常 `ningbo` 服务时，点击屏幕“启动”，无输入参数则默认正转 512 步，步间隔取 `stepper.default_speed_ms`（当前 8 ms，约 4 秒）。点击“停止”可中断；屏幕实际下发的方向和步数会覆盖默认值。这个行为与持续正转测试脚本不同。

## 早期独立实验

- `standalone/oled/`：`oled_hello.py`、`oled_raw.py`、`oled_test.py`、`oled_text.py`，保留原始 I2C 实验代码。
- `notebooks/StepperMotor_Xavier_NX_Demo.ipynb`：原始步进电机实验。
- `notebooks/Untitled.ipynb`、`notebooks/Untitled1.ipynb`：原始未命名实验，未经本次运行验证。

这些脚本和 Notebook 沿用当时的总线、地址、引脚和依赖，运行前应与当前 `JETSON/Sensor/Config.py` 核对。

## 历史文件与结果

`archive/jetson_20261015/home_files/` 保存 `apply_servo_simple.py`、`apply_xfyun_update.py`、独立 DHT11 实验、旧版配置和桥接文件等。补丁代码依赖当时的工程内容，有些配套 payload 已不存在，仅供追溯。

`artifacts/jetson_20261015/` 保存本次收集的日志、JSON/JSONL、录音、摄像头图片等。`archive/` 与 `artifacts/` 在 Git 中忽略，但文件已保存在本地，并包含在原始远端导出包中。
