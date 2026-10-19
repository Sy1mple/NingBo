# 宁波硬件工程

> **当前运行版本（2026-10-19）**：[ningbo/](ningbo/) 是从 `192.168.1.106:/opt/ningbo` 同步的场景模块化工程，本地对应 `NingBo_Minimal_NoOLED_20261016/ningbo`。入口为 `ningbo/main.py`，配置为 `ningbo/config.py`；下文根目录工程保留为旧版参考。设备与串口屏现已拆为两个独立服务，见 [独立服务说明](ningbo/deploy/SCREEN_SERVICES.md)。同步与验证记录见 [docs/SOURCE_SYNC_20261019.md](docs/SOURCE_SYNC_20261019.md)。

项目由开发板设备服务、业务桥接和 JGUS 串口屏服务组成。正式入口为 `main.py`，所有传感器、执行器和串口屏通信参数集中在 `JETSON/Sensor/Config.py`；屏幕的 VP 地址与按钮返回值集中在 `Serial_Screen/screen_service/screen/addresses.py`。

整理后的目录与测试入口见 [项目索引](PROJECT_INDEX.md) 和 [硬件测试与调参](tools/README.md)。

## 工程结构

```text
NingBo/
├── main.py                     # 正式启动和配置检查
├── application.py              # 工程生命周期与主循环
├── requirements.txt            # 开发板运行依赖
├── JETSON/
│   ├── Voice/
│   │   ├── service.py          # USB 采音/识别线程与结果队列
│   │   ├── recognizer.py       # 识别后端选择，禁止自动 CPU 后备
│   │   ├── whisper_recognizer.py # CUDA Whisper 推理、分句与抗混叠重采样
│   │   └── models/            # 已下载的中文模型及来源说明
│   ├── Vision/
│   │   ├── service.py         # 摄像头录入/识别任务与结果队列
│   │   ├── engine.py          # YuNet 检测、SFace 对齐与特征
│   │   ├── tensorrt_models.py # YuNet 后处理和 SFace 对齐，网络前向由 TensorRT 执行
│   │   ├── library.py         # SQLite 人员与多帧特征库
│   │   ├── features.py        # 特征校验与模型文件指纹
│   │   └── models/            # 预训练 ONNX 模型及许可证
│   ├── Inference/
│   │   ├── tensorrt_runner.py # TensorRT 8.x、CUDA 缓冲、GPU/DLA 与本机引擎缓存
│   │   └── checks.py          # 模型实际前向检查，不打开外设
│   └── Sensor/
│       ├── Config.py           # 所有设备配置与校验
│       ├── service.py          # DeviceService 业务接口
│       ├── manager.py          # 采集、控制、状态及资源关闭
│       ├── diagnostics/
│       │   ├── service.py      # 状态检测任务调度
│       │   ├── sensor_checks.py   # 传感器有效性检测
│       │   ├── actuator_checks.py # 执行器状态检测
│       │   └── definitions.py # 分组及汇总规则
│       ├── resources.py        # 共享 GPIO / I2C
│       ├── pinmap.py           # 根据配置生成排针说明
│       ├── sensors/            # 温湿度、光照、烟雾、超声波、电流、USB 麦克风/摄像头
│       └── actuators/          # 舵机、步进、风扇/锁/蜂鸣器、RGB、OLED
├── Serial_Screen/
│   ├── device_bridge.py        # 屏幕按钮与设备业务转换
│   ├── control_bridge.py       # 舵机/步进参数与执行器启停
│   ├── access_bridge.py        # 门禁命令与识别结果回调
│   ├── voice_bridge.py         # 语音按钮与识别文本回写
│   ├── diagnostics_bridge.py   # 状态检测按钮与检测结果回写
│   └── screen_service/screen/
│       ├── addresses.py        # VP 地址与按钮返回值
│       ├── message_parser.py   # 业务数据转地址和值
│       ├── text_updates.py     # 文本编码、缓冲区及 VP 占用校验
│       ├── screen_sender.py    # JGUS 帧和串口写入
│       ├── screen_events.py    # 串口按钮回传解析
│       └── service.py          # 唯一的屏幕串口持有者
├── business/
│   ├── access_control.py       # 可选的门锁限时联动
│   └── rgb_feedback.py         # 体温、距离、录入和识别结果的灯色规则
├── data/faces.sqlite3          # 本地永久人脸库，初始无人员
├── data/engines/               # 在目标 Jetson 首次运行时生成的 TensorRT 引擎
└── tests/                      # 配置、协议、故障和按钮流程测试
```

驱动包含 DHT11、BH1750、超声波、烟雾 UART、舵机、步进电机、风扇、电磁锁、蜂鸣器、RGB、OLED 和 ADS1115 电流读取。舵机脉冲由硬件 PWM 输出，独立线程只更新往返目标角度；步进采用独立控制线程；传感器采集由设备管理服务调度。

## 配置与启动

Linux 部署包可在支持的 Xavier NX 系统上执行 `bash deploy.sh` 安装环境，再执行 `bash run.sh` 启动；版本范围、联网要求与参数见 `deploy/README_LINUX.md`。接线文档放在部署包 `docs/`。脚本支持 `--plan` 仅读取检查和 `--check-only` 验证已安装环境。

当前 GPIO 使用 Jetson 40 针排针的 `BOARD` 物理编号，接线以 `Config.py` 为唯一配置来源：

| 设备 | BOARD 物理引脚 |
| --- | --- |
| DHT11 | 37（原生 `libgpiod` 微秒采样；实机为 gpiochip1 line 151） |
| 超声波 TRIG | 32 |
| 超声波 ECHO | 33 |
| 舵机信号 | 15 |
| 蜂鸣器 | 40 |
| 电磁锁 | 29 |
| 风扇 | 31 |
| RGB 红 / 绿 / 蓝 | 12 / 35 / 38（共阴，高电平点亮） |
| 步进电机 | 13 / 16 / 18 / 22 |

串口屏默认使用 `auto` 扫描并验证协议应答，烟雾传感器串口为 `/dev/ttyTHS0`（物理 8=TX、10=RX），需交叉连接并关闭 nvgetty 串口登录；详见 `SMOKE_UART_INTEGRATION.md`。修改接线后必须同步修改 `JETSON/Sensor/Config.py`，启动时的配置校验会检查 GPIO 重复占用。

只检查配置结构，不打开任何硬件：

```powershell
python main.py --check-config
```

在开发板上安装依赖后启动：

```bash
python -m pip install -r requirements.txt -c constraints-jetson.txt
python main.py
```

串口屏端口和波特率可以临时覆盖：

```bash
python main.py --port auto --baudrate 115200
```

上述基础依赖之外，人脸和语音需要按下一节安装 JetPack 的 TensorRT 与 CUDA PyTorch、Whisper。启动顺序为打开屏幕串口、启动设备服务、等待按钮事件。设备管理器在后台采样；点击场景“开始检测”后显示实时读数，并启用该场景的 RGB 联动；点击“停止检测”后对应字段写 0、停止刷新并释放该场景的灯色。按 Ctrl+C 结束，程序通知识别服务停止、停止执行器、等待线程退出并关闭连接。

## Xavier NX 硬件推理部署

用户已确认计算模块是 Jetson Xavier NX。[微雪的规格](https://www.waveshare.com/jetson-xavier-nx-dev-kit.htm)列出最高 21 TOPS、Volta GPU 和两颗 NVDLA。TOPS 是特定精度及功耗模式下的峰值指标，不能据此推算本项目帧率，也不是所有算子都能执行在 DLA 上。

默认人脸检测和特征提取使用 **TensorRT FP16 GPU**，默认中文语音使用 **Whisper CUDA FP16**。神经网络前向不设 CPU 自动后备；缺少 CUDA、TensorRT、匹配的 PyTorch 或构建失败时报告错误，由串口屏桥接撤销成功条件。CPU 负责串口、采音、GPIO、图像缩放/对齐、音频分句/重采样、特征比较与业务阈值判断。

TensorRT 适配目标为 Xavier NX 的 **JetPack TensorRT 8.x**；Whisper 依赖配置面向 JetPack 5 的 Python 3.8~3.10。先在板上确认当前版本，不直接安装最新通用 TensorRT/PyTorch 包，也不在 Windows 生成 Jetson 引擎：

```bash
cat /proc/device-tree/model
head -n 1 /etc/nv_tegra_release
dpkg-query -W nvidia-jetpack libnvinfer8 python3-libnvinfer
python --version
python -c "import tensorrt as trt; print(trt.__version__)"
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

TensorRT 使用 JetPack 提供的 Python 包，运行项目的 Python 环境必须能导入它。CUDA PyTorch 按 [NVIDIA Jetson 安装说明](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html)选择与实际 JetPack/Python 匹配的 NVIDIA wheel；不要用通用 `pip install torch` 替换板上的版本。板上 JetPack 版本目前未提供，所以项目没有硬编码一个未经核对的 wheel 下载地址。

确认 CUDA PyTorch 和 TensorRT 后安装普通依赖及 Whisper，Whisper 单独使用 `--no-deps` 保留 NVIDIA torch：

```bash
sudo apt-get install libportaudio2
python -m pip install -r requirements.txt -c constraints-jetson.txt
python -m pip install -r requirements-whisper.txt -c constraints-jetson.txt
python -m pip install openai-whisper==20231117 --no-deps

# 在板上真正运行检测、特征提取和语音模型的前向；不打开摄像头/麦克风/GPIO/串口
python main.py --check-acceleration
```

检查返回 JSON，两个组件均为 `ok: true` 才说明这次实际前向成功，进程退出码 0 表示通过；失败返回非零，并列出真实错误。首次人脸检查会编译两个 ONNX 模型，可能耗时较长；生成的引擎按模型内容、输入形状、CUDA/TensorRT 版本、主机、精度和 DLA 配置缓存到 `data/engines/`。ONNX 权重可复制，engine 应在目标板生成；缓存不兼容时报错，不自动改为 CPU。

模型配置仍集中在 `JETSON/Sensor/Config.py`：

| 配置 | 默认值 |
| --- | --- |
| `face.backend` / `face.precision` | `tensorrt` / `fp16` |
| `face.cuda_device` / `voice.cuda_device` | `0` / `0` |
| `face.dla_core` | `None`，使用 GPU |
| `face.dla_gpu_fallback` | `True`，DLA 不支持的层可交给 GPU |
| `face.workspace_mb` | `256` |
| `voice.backend` | `whisper_cuda` |

运行时可传 `--cuda-device 0`，同时覆盖人脸和语音的 GPU 编号。需要尝试 DLA 时：

```bash
python main.py --check-acceleration --face-dla-core 0
python main.py --port auto --camera-device auto --voice-device auto --face-dla-core 0

# 必须所有网络层都符合 DLA 时使用；不支持就构建失败
python main.py --check-acceleration --face-dla-core 0 --face-dla-strict
```

DLA 选择使用 [TensorRT 的 DLA 构建接口](https://docs.nvidia.com/deeplearning/tensorrt/archives/tensorrt-861/developer-guide/index.html)。两个人脸模型能有多少层放上 DLA，需要查看目标板的构建日志；默认仍用 GPU。Whisper 的 Transformer 网络当前使用 GPU，不承诺可放在 DLA。FP16 表示允许 TensorRT 选择半精度层，具体层精度由构建器决定。

离线 Windows 开发时可以**明确选择** `--face-backend opencv_cpu`；旧 Vosk 仅通过 `--voice-backend vosk --voice-model /path/to/vosk-model-small-cn-0.22` 启用，并另装 `vosk`。这些调试选项不会改变正式默认配置，CPU 调试后端不能通过 `--check-acceleration`。

## 串口屏到真实设备的桥接

调用链为：串口屏回传帧 → `ScreenEventParser` → `ScreenDeviceBridge` → `DeviceService` → 真实驱动/GPIO；传感器采样结果同时用于屏幕刷新和业务判断。业务模块只调用设备 API，接线和有效电平由驱动读取 `Config.py`。

风扇启停按钮直接调用风扇输出。舵机页面只有启动/关闭按钮，角度框用于显示：点击启动后依次执行 45°→135°→90°，每个位置保持 4 秒并重复。三个位置由 `servo.sweep_min_angle`、`servo.sweep_max_angle`、`servo.default_angle` 配置，停留时间由 `servo.sweep_interval_seconds` 配置。每个位置直接调用 `PWM.start`，使用此前实机转动成功的 50 Hz、1000/2000/1500 微秒输出方式。屏幕随目标变化刷新，向 `0x1061` 按 10 倍缩放回写（90° 写入 900）。关闭按钮停止循环及 PWM，同时向角度显示 VP 写入 0，屏幕角度清零。MG996R 没有角度反馈线，屏幕显示目标角度。步进页面无需输入也可启动：未下发参数时默认正转 512 步，收到 `0x1062` 方向或 `0x1063` 步数后使用对应参数。启停按钮需开启“下发串口”；可编辑的步进输入控件如需覆盖默认值，也应开启该选项。方向 Word 默认 `0=正向、1=反向`，可通过 `screen_control` 配置。

舵机目标角度决定脉宽，`0x1060` 不作为输入指令。启动立即下发第一个位置；循环在后台执行，关闭按钮可随时停止，不必等待 4 秒结束。步进速度采用 `stepper.default_speed_ms`，单位为每一步的毫秒间隔；屏幕速度控件的 VP 尚未登记。停止按钮调用单设备停止接口，不影响其他执行器。

RGB 规则在 `business/rgb_feedback.py`，阈值和等待颜色在 `Config.py` 的 `rgb_feedback`：

| 场景 | 绿灯 | 红灯 |
| --- | --- | --- |
| 人体测温 | 有效读数不高于 `37.5°C` | 有效读数高于 `37.5°C` |
| 距离检测 | 有效距离 ≥ `8 cm` | 有效距离 < `5 cm` |
| 门禁识别 | 真实识别结果 `recognized=True` | 真实识别结果 `recognized=False` |

默认有效体温输入范围为 `35~45°C`，默认读数有效期为 5 秒。缺失、故障、超出输入范围或过期读数亮蓝灯，不写入测量值。多个场景同时使用 RGB 时，红灯优先，其次等待色蓝灯，全部有效且正常时绿灯；停止一个场景保留其余场景的判断，全部停止后熄灯。颜色不变时不重复写 GPIO；GPIO 写失败会记录日志并在后续主循环重试。

距离检测 RGB 为三档：有效距离 < `rgb_feedback.distance_danger_cm`（默认 5 cm）红灯，5 cm ≤ 距离 < `rgb_feedback.distance_caution_cm`（默认 8 cm）蓝灯（与等待/无效共用蓝色），距离 ≥ 8 cm 绿灯。测距蜂鸣器规则在 `business/proximity_alarm.py`：点击距离检测“开始检测”后，有效距离小于 `rgb_feedback.distance_danger_cm`（默认 5 cm）时蜂鸣器鸣响；距离达到 5 cm、读数失效/过期或点击停止检测时关闭。与距离红灯共用危险阈值，其他场景的红灯不会触发测距蜂鸣器。开关配置为 `ultrasonic.near_buzzer_enabled`，蜂鸣器引脚及有效电平仍由 `buzzer` 配置管理（默认 BOARD 40、低电平触发 `active_high=False`）。输出失败会记录日志并在后续循环重试。

`37.5°C` 是本项目可调的教学演示分界，需要结合实际模块和测温方式校准；正常体温随个体和时间变化，参见 [NHS 体温说明](https://www.nhs.uk/symptoms/fever-in-adults/)。设备自检的“正常”表示采集/驱动有效，高温或近距离的红灯由业务规则独立判断。

人体测温默认启用内置 GY-906/MLX90614 驱动，与 BH1750 共用 I2C8，地址 0x5A，严格校验 PEC。`temperature` 返回目标表面摄氏温度，`ambient_temperature` 返回环境温度；详见 `GY906_INTEGRATION.md`。其他型号仍可通过 `DeviceService(config=cfg, body_temperature_driver=driver)` 注入真实驱动。

默认门禁服务已接入 `JETSON/Vision/FaceService`。也可通过 `NingBoApplication(access_service=service)` 替换服务，或通过旧接口 `NingBoApplication(access_handler=handler)` 接入自定义流程。旧接口的 `handler(event_name)` 处理 `access.start`、`access.stop`、`access.face_enroll`；同步识别结果返回 `{"recognized": True/False}`，异步回调保存启动事件的 `session_id` 后调用 `app.report_access_result(recognized, session_id)`。回调只入队，RGB 由主循环更新；停止后的旧会话会被丢弃。`ok` / `success` 命令应答不会当作识别成功。

普通 USB 麦克风的采音、中文识别及屏幕文本回写已接入默认主程序，具体配置和部署见下一节。`NingBoApplication(voice_service=service)` 可替换识别服务；旧的 `voice_handler` 启停接口仍可注入自定义服务。

## USB 麦克风与语音交互

当前默认语音识别后端为 xfyun，使用讯飞语音听写流式版免费额度，完整说明在 deploy/README_VOICE_XFYUN.md。执行 .venv/bin/python deploy/configure_voice.py 隐藏输入三项密钥，或编辑根目录 .env.voice（权限600）。密钥不在 Config.py 中。麦克风按名称 AIMIC-M4 选择，40ms 采样块连续滤波转16k；检测到说话才建立会话，中间结果及动态修正回写原0x1064，停止按钮取消会话并清空文字。

本项目每天最多450次连接，SQLite 持久化，断电重启不清空同日次数；达到上限停止调用，不自动购买或切换其他接口。本地计数无法感知同一 AppID 的其他程序用量，建议实验箱使用独立应用并仅开通默认免费服务。云端需要网络，未填密钥时语音报配置错误，其他功能正常。本地 Whisper 只在显式选择 --voice-backend whisper_cuda 时使用。旧段落中的 Whisper 模型说明仅适用于该可选后端。

语音字库已在原屏幕工程中配置并下载，固定中文测试已确认正常。


当前实机 USB 音频设备为 AIMIC-M4，默认按名称选择；voice.input_gain=30 补偿较低音量，放大限幅后分句，换设备时应按实测音量调整。开始后等待模型加载及采音就绪。日志记录设备、采音就绪、识别完成和文字写屏。

Whisper 接收 CUDA 音频 Tensor，STFT/梅尔特征及模型在 GPU 上执行，重采样使用 SciPy。实测 CPU 梅尔特征存在 NaN，CUDA 路径已验证有限值。若识别概率出现 NaN/Inf 则明确报错。


语音链路为：开始按钮 → `VoiceScreenBridge` → `VoiceService` 后台线程 → `UsbMicrophone` 单声道 PCM → 分句与 16 kHz 重采样 → CUDA Whisper 中文识别 → 主循环写入文本 VP。项目启动时不打开麦克风，只有点击语音开始按钮才初始化模型和采音；重复开始不创建第二个线程。停止按钮关闭输入流、取消未完成结果、等待线程退出并清空文字框。旧会话的文字不会在重新开始后显示。

| 功能 | 串口屏地址 | 返回值 |
| --- | --- | --- |
| 开始聆听 | `0x3080` | `0x5080` |
| 停止聆听 | `0x3090` | `0x5090` |
| 识别文本显示 | `0x1064` | 中文编码后的字符串 |

语音设置统一在 `JETSON/Sensor/Config.py` 的 `VoiceConfig`：

| 配置 | 默认值 / 含义 |
| --- | --- |
| `enabled` | `True` |
| `input_device` | `"USB"`，可传音频编号、思必驰设备名称关键字、`hw:声卡号,设备号` 或采音节点 |
| `sample_rate` | `None`，使用麦克风默认采样率 |
| `block_duration_ms` | `100`，每个采音块约 100 ms |
| `audio_queue_blocks` | `128`，限制 GPU 推理期间积压的音频；溢出时明确失败 |
| `show_partial` / `partial_interval` | 默认关闭临时结果；Whisper 当前在分句结束后显示完整文本 |
| `model_path` | 自动定位项目内 `JETSON/Voice/models/whisper-tiny.pt` |
| `language` | `zh`，固定中文识别 |
| `silence_rms` / `silence_seconds` | `0.015` / `0.6`，能量阈值与分句静音长度，需按噪声校准 |
| `minimum_utterance_seconds` / `maximum_utterance_seconds` | `0.5` / `8`，不足时不推理、连续讲话过长时分段 |
| `text_encoding` | `gbk`，可改 `gb2312` 或 `utf-16-be` 并同步屏幕设置 |
| `text_words` | `32`，占 64 Byte / 32 个 VP Word |

在 VisualBoard 中，`0x1064` 必须使用**文本显示控件**，编码设为 GBK，文本长度设为 64 Byte，并配好对应中文字库。该缓冲区占用 `0x1064~0x1083`，不要在这个范围新增其他变量。最多写入 31 个双字节汉字并保留 `FFFF` 结束标记；超长文本按完整字符截断，不会拆开汉字。每次发送会覆盖整个缓冲区，较短的新文字不会留下旧尾巴；地址跨度与其他已登记 VP 重叠时会拒绝写入。两枚语音启停按钮启用“下发串口”。GBK 与文本长度的设置依据可参见 [DGUS 官方文本控件说明](https://www.dwin.com.cn/dwin_news_dguspxskjwtdhtbxswbltbxs.html)；JGUS 实际显示效果仍需按所用屏幕和字库确认。

已下载 [Whisper 官方多语言 tiny 模型](https://github.com/openai/whisper)，复制到 Jetson 时包含 `JETSON/Voice/models/whisper-tiny.pt` 和许可证，运行时不联网下载或上传音频。tiny 用于较小模型的部署起点，中文准确率和延迟需在思必驰设备上测试；可传入已下载的 base/small 多语言 `.pt` 模型。旧 Vosk 权重保留用于显式 CPU 调试，默认语音不再使用它。

Jetson 部署时安装音频库和项目依赖：

```bash
sudo apt-get install libportaudio2
python -m pip install -r requirements.txt -c constraints-jetson.txt
python main.py --list-audio-devices
```

Linux 的 PortAudio 库需通过系统包安装，参见 [sounddevice 安装说明](https://python-sounddevice.readthedocs.io/en/latest/installation.html)。音频列表查询不录音，也不打开 GPIO。若没有设备名称包含“USB”，或匹配到多个设备，按列表中的实际名称关键字或编号设置 `voice.input_device`；程序不会自动选择其他输入设备。编号来自运行识别服务的同一台 Jetson，Windows 上的编号不能直接沿用。

当前硬件是思必驰麦克风、扬声器一体设备，麦克风属于音频输入。`--voice-device` 与 `--voice-input-device` 是同一个参数的两种写法，每次启动均可覆盖配置。串口屏使用独立的 `--port /dev/ttyUSB*` 参数。

USB 音频在 Linux 上可通过以下命令检查：

```bash
ls /dev/snd/
arecord -l
python main.py --list-audio-devices
```

`arecord -l` 的声卡号和设备号组合为 `hw:C,D`，与程序音频列表的编号是两套编号；例如 `card 2, device 0` 对应 `hw:2,0` 和采音节点 `/dev/snd/pcmC2D0c`，不能直接把声卡号当作 `--list-audio-devices` 的输入编号。可按实际结果选择任一种方式传入：

```bash
python main.py --port auto --voice-device auto
python main.py --port auto --voice-device /dev/snd/pcmC2D0c
```

程序把采音节点映射到同一声卡、同一设备的 ALSA 输入，不直接读取设备文件；`p` 后缀是播放节点，录音应使用 `c` 后缀。设备列表会同时打印输入和输出通道；语音识别使用输入端，扬声器播放另走输出端。

例如，确认 Jetson 上输入设备编号为 2 后启动：

```bash
python main.py --voice-device 2
```

也可覆盖模型位置：

```bash
python main.py --voice-device "USB Audio" --voice-model /path/to/whisper-base.pt
```

USB 断开、模型缺失、依赖缺失或音频队列溢出会记录真实错误并停止本次语音任务，`app.voice.snapshot()` 可读取阶段及错误信息。USB 输入和中文模型验证后，点击开始聆听并讲话，确认屏幕显示识别文字；再点击停止，确认文字清空且输入流关闭。具体识别准确率取决于麦克风、噪声和模型，需要实机测试。

## USB 摄像头、人脸录入与人脸库

串口屏沿用现有三个按钮，不需要增加姓名输入控件或新页面：

| 功能 | 键值返回地址 | 返回值 |
| --- | --- | --- |
| 人脸录入 | `0x3010` | `0x5010` |
| 启动门禁识别 | `0x3020` | `0x5020` |
| 停止门禁识别/取消录入 | `0x3030` | `0x5030` |

主程序默认创建视觉服务，启动时不打开摄像头。点击人脸录入后，工作线程加载模型、打开 USB 摄像头、收集 5 帧清晰且一致的单人人脸，事务保存到 `data/faces.sqlite3`，随后释放摄像头。默认采样间隔至少 0.3 秒，20 秒内未采集完整则失败；多人、模糊、过小的人脸不会入库，对象中途变化会拒绝录入。录入相似度达到 0.6 的已有人员时返回原编号，避免重复建档。录入时蓝灯，完成绿灯、失败红灯，结果灯色保留 2 秒后释放；多个场景同时工作时仍遵循 RGB 优先级。结果与编号在程序日志输出，现有屏幕没有录入结果 VP，因此不额外发送姓名或状态文本。

平时只按屏幕的人脸录入按钮，人员自动命名为“人员0001”“人员0002”。**姓名不影响识别，可以始终不填。** 需要姓名时，在 Jetson 的终端或电脑通过 SSH 连接到 Jetson 输入；在项目根目录执行：

```bash
# 查看人员编号和姓名；不打开摄像头、GPIO 或串口
python main.py --list-faces

# 将编号 1 的显示姓名改为张三；不必重新录入
python main.py --rename-face 1 "张三"
```

改名后，正在运行的门禁需在屏幕上停止再启动，以重新载入人脸库。操作同一个库时，所有命令使用相同的 `--face-db /path/to/faces.sqlite3`；默认库路径相对于项目自身定位，不受终端工作目录影响。也可以启动时提供下一位成功新录入的姓名，此姓名用过一次后恢复自动编号；重复录入已有人员不会修改其姓名：

```bash
python main.py --camera-device auto --enroll-name "张三"
```

摄像头尚未插入时可以先检查配置、初始化空库；正式选择设备应在 Jetson 插入摄像头后执行：

```bash
python main.py --check-config
python main.py --init-face-db
python main.py --list-cameras
ls /dev/video*
ls -l /dev/v4l/by-id/

# 按实际节点传入；麦克风和串口屏分别指定
python main.py --port auto --voice-device auto --camera-device auto
```

`--camera-device` 接受编号（例如 `0`）、`/dev/video*` 或稳定的 `/dev/v4l/by-id/*` 路径。USB 设备重新插拔后编号可能变化，建议使用实际列出的 by-id 采集节点；一个摄像头可能列出多个节点，需选择能输出视频的节点。Windows 摄像头可用编号 0；Jetson 的串口、音频和视频编号须分别在该板上确认。查询节点不打开摄像头，读取失败会报告真实错误，不自动换到另一台摄像头。

模型使用已下载的 [YuNet / SFace](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html)，默认由 TensorRT 在 GPU 上执行两个网络，无需自行训练。部署时复制 `JETSON/Vision/models/` 和需要保留的 `data/faces.sqlite3`，模型来源、校验值和许可证见 `JETSON/Vision/models/NOTICE.md`。OpenCV 仅负责图像处理和 NMS，正式推理不依赖 OpenCV 的 CUDA 编译选项；pip 的 CPU OpenCV 包也不会把 TensorRT 网络切回 CPU。检测输入保持比例缩放后填充到 YuNet 的原生 640×640 固定输入；该 ONNX 带固定 reshape，TensorRT 构建不能直接改为 320。SFace 使用五点对齐后的 112×112 RGB 输入。

启动门禁后持续比较库内人员，默认余弦相似度至少 0.363，第一候选与第二候选差至少 0.05，同一人员连续确认 3 帧才亮绿灯；未匹配或多人亮红灯，无人或画面质量不足亮蓝灯。默认检测图像宽度 640、检测置信度 0.85，人脸像素至少 80，清晰度阈值 50。所有参数统一在 `Config.py` 的 `camera`、`face` 中；相似度、清晰度和连续帧设置需要实机校准。人脸库为空会提示先录入，不打开摄像头进行空库识别。

录入和识别共用一个摄像头任务。正在识别时先按停止再录入；停止取消未完成结果、等待工作线程自行释放摄像头，过期结果不会改变灯色。读取阻塞导致线程不能及时退出时，暂时拒绝再次启动，避免同时占用设备；`app.face.snapshot()` 可读取阶段和故障。视觉线程不直接操作 GPIO，RGB 与可选门锁动作由主循环执行。一个主循环收到多条识别记录时只采用最新状态，模型或摄像头错误优先撤销之前的成功条件。

默认 `face.unlock_on_match=False`，只联动 RGB；确认实际锁模块通电/断电的开锁含义后可启用，成功时限时通电 `unlock_seconds=3`，失败、停止或故障会提前撤销。当前模型没有活体检测，不能把照片攻击防护当作已实现功能。人脸库保存姓名、录入时间和 128 维特征，不保存原始摄像头照片；模型文件指纹不一致或特征损坏时拒绝识别。

## 业务调用

```python
from JETSON.Sensor import DeviceService

with DeviceService() as devices:
    print(devices.snapshot())
    devices.control("fan", on=True)
    devices.control("servo", angle=90)
    devices.control("stepper", dir_="fwd", n=100, spd=8)
```

工程内的业务模块应共用 `application.devices` 实例。读数失败时字段置 `None` 并记录错误；控制失败会抛出异常。驱动导入时不打开硬件，`start()` 才初始化实际资源。

## 待确认设备

人体温度已接入 GY-906/MLX90614，默认启用，采样、状态检测、屏幕和 RGB 共用现有调用链。烟雾 UART 已接入 `FF AA H L 3C` 流式协议，显示原始浓度，不进行未确认的百分比或 ppm 换算。OLED 默认 SH1106，需要核对实际芯片。ADS1115 电流采样默认关闭，确认通道和分流电阻后再开启。舵机使用物理 15 脚的硬件 PWM，50 Hz/20 ms 周期；当前 45°、90°、135° 分别对应 1000、1500、2000 微秒，已在目标板测试转动。端点脉宽配置为 500~2500 微秒，实际机械角度仍需按舵机校准；屏幕显示下发目标，不是角度反馈。状态检测读取 PWM 控制器周期、脉宽和使能状态，往返时也检查角度更新线程；脉冲持续输出由硬件完成。

设备模式下没有有效读数时，串口屏保持原显示值；停止按钮会清零。集成业务沿用合并包的光照百分比定义，量程在 `light.display_full_scale_lux` 配置。

## 状态检测与串口屏按钮

状态检测实现在 `JETSON/Sensor/diagnostics/`，配置仍统一在 `Config.py`。传感器检测、执行器检测和任务调度独立实现。`DeviceService.check(name)` 检测单个设备，`check_all(group="all")` 同步检测一组设备；串口屏按钮使用 `request_diagnostics()` 在后台执行，主循环通过 `diagnostic_snapshot()` 读取进度与结果。重复点击时只保留一个正在执行的任务。

温湿度、光照和超声波检测读取有效值及驱动校验结果。烟雾仅在收到完整 FFAA 帧且数据未过期时报告正常，超过 5 秒没有有效帧则报告错误；保留 raw 模式供协议调试。人体温度使用内置 MLX90614 驱动检测真实读取和 PEC 校验结果，其他型号未提供驱动时拒绝启用。OLED 属于输出设备，其检测当前验证 I2C 应答。

执行器检测覆盖舵机、步进电机、蜂鸣器、风扇、电磁锁、RGB 和 OLED。舵机与步进检查控制线程；开关输出及 RGB 比较指令与 GPIO 读回电平；电流反馈按通道上下限判断运行、空闲、运行电流不足、过电流或关闭残余电流。GPIO 读回使用 [NVIDIA Jetson.GPIO 的 input 接口](https://github.com/NVIDIA/jetson-gpio/blob/master/lib/python/Jetson/GPIO/gpio.py#L326)。

电流默认采样 3 次，使用中位数判断运行下限和关闭电流；已配置运行上限时，任意样本越限均报告异常。采样次数和间隔位于 `actuator_diagnostics`；每个通道的运行下限 `threshold_ma`、可选运行上限 `max_current_ma`、可选关闭上限 `off_current_ma` 位于 `current.channels`。这些阈值需要按实际模块标定。反馈通道未启用或未配置时报告未配置。关闭且电流低时报告空闲。

每个执行器结果含 `driver`、`signal`、`feedback` 子项及采样电流，检测失败后可以重新检测并更新恢复结果。执行器的 `check_scope=electrical` 表示验证到电气状态；舵机到位、步进实际位移、风扇转速等仍需相应物理反馈。OLED 的 `check_scope=communication` 表示 I2C 应答检测。

执行器接口可单独调用：

```python
devices.check_actuator("fan")
devices.check_actuators()
devices.request_diagnostics(group="actuators")
```

状态页只设置三个检测按钮，不再为每台设备设置独立检测按钮：

| 按钮 | 键值返回地址 | 返回值 |
| --- | --- | --- |
| 一键检测 | `0x9000` | `0x8000` |
| 传感器检测 | `0x9001` | `0x8001` |
| 执行器检测 | `0x9002` | `0x8002` |

当前状态结果 VP 与页面中的 11 个设备框一一对应：

| 设备 | 结果 VP | 设备 | 结果 VP |
| --- | --- | --- | --- |
| 温湿度 | `0x6000` | 舵机 | `0x6005` |
| 光照 | `0x6001` | 步进电机 | `0x6006` |
| 烟雾 | `0x6002` | 蜂鸣器 | `0x6007` |
| 人体测温 | `0x6003` | RGB 指示灯 | `0x6008` |
| 超声波 | `0x6004` | 风扇 | `0x6009` |
|  |  | 电磁锁 | `0x6010` |

电流模块和 OLED 仍可由后台诊断服务检测，但当前串口屏状态页不显示，也不分配结果 VP。检测进度和总状态控件仍未填写实际 VP，因此 `STATUS_CHECK_PROGRESS_ADDR` 与 `STATUS_CHECK_OVERALL_RESULT_ADDR` 暂时为 `None`；在 VisualBoard 确认地址后再填写。未填地址不会生成写屏帧，地址与现有变量或按钮冲突时会在启动导入阶段报错。

舵机、步进电机和语音页面已登记以下业务变量：舵机方向 `0x1060`、舵机目标角度 `0x1061`、步进方向 `0x1062`、步数 `0x1063`、语音识别文本 `0x1064`。对应启停按钮的键值返回地址和值统一维护在 `addresses.py`。

状态显示采用单 Word 数值，可在屏幕端绑定数字或图标：

| 数值 | 状态 |
| --- | --- |
| 0 | 未检测或未知 |
| 1 | 当前检测项正常 |
| 2 | 当前检测项异常 |
| 3 | 型号、协议或反馈未配置 |
| 4 | 设备未启用 |
| 5 | 检测中 |
| 6 | 执行器空闲，尚未验证运行反馈 |

数据采集值位于快照的 `sensors`，输出设备状态位于 `actuators`。状态检测报告单独保存，不与温度、距离等业务显示值共用 VP。

## 代码验证

```powershell
python -m unittest discover -s tests -v
```

单元测试使用固定输入与接口替身验证真实业务代码，不打开外设。开发板接线、传感器精度和执行器动作需实机验证。

后台运行收到 SIGTERM/SIGINT 时，请求主循环退出并按正常生命周期停止 PWM、释放引脚和关闭串口。

屏幕语音文字中文显示需下载中文字库。Windows 修复副本 Smart_Experiment_Kit_voice_fix 使用 70_GB2312_24_华文中宋.hzk，中文字库编号 70、字号 24×24、ASCII 字库 0、GB2312 编码。旧项目只有 ASCII 字库，实测英文正常而中文异常。必须把修复副本的字体一起下载到屏幕，Jetson 端已匹配 gb2312。采音缓冲为 0.5 秒，偶发 input overflow 记录警告并继续采集。

## USB 设备自动发现

默认直接运行 `bash run.sh`，或显式使用 `--port auto --camera-device auto --voice-device auto`。开机服务不再写死端口参数。

- 串口屏在启动时枚举 USB 串口，跳过已配置的烟雾串口，验证 0x1000、0x1030 两个 VP 的只读应答；ASCII 日志、请求回显和不完整应答不会通过。识别后保留连接和探测期间收到的按钮帧。
- 摄像头在启动视觉任务时重新扫描 USB V4L2 图像节点，依据当前节点的 device_caps 排除元数据节点，再确认有效首帧；同一真实节点的不同别名去重。
- 语音在启动采音时重新按名称/能力选择 USB 输入，优先 AIMIC-M4/AISPEECH，排除板载、HDMI 和默认混音设备。具备输入/输出能力的一体设备会同时记录输出端名称；现有识别流程只使用输入，不新增播报。

USB 编号或插孔变化不需要修改默认配置。找到多块应答屏幕、多台物理摄像头或多个匹配音频输入时，会明确要求用现有参数指定设备，避免任意选择第一项。`--list-cameras`、`--list-audio-devices` 可检查当前枚举。运行中拔出设备仍可能中断当前任务；屏幕串口错误由服务退出后重新扫描，视觉/语音任务再次启动时重新选择设备。
