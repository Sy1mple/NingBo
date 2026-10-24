# 宁波实训箱项目 README

> 2026-10-22：状态检测各设备独立启动、独立提交结果；执行器等待动作和采样时不再持有全局控制锁。ADS1115 的每次配置和转换仍通过共享 I²C 锁保护。门锁结果仍写 `0x6010`，串口屏服务回读确认该 VP，读回不符最多重发两次；所有检测结果完成后另做两次有限刷新。检测超时返回异常并取消该项动作，保留未退出线程的资源，禁止重复启动同一项。

> 2026-10-19：正式运行已拆分为 `ningbo-new.service`（设备业务）和 `ningbo-screen.service`（串口屏通信）。屏幕离线或重启不再停止设备采样。安装、手动启动与参数变更见 [独立服务说明](deploy/SCREEN_SERVICES.md)。

## 步进电机启停与状态写屏

步进桥接已参考舵机：启动按钮 `0x3060 / 0x5060` 在控制成功后立即写入方向中文和剩余步数；主循环每 0.2 秒按实际驱动快照刷新，状态未变化不重复发送。手动停止 `0x3070 / 0x5070` 或步数自然走完时写入“停止”和 0，并停止刷新。方向使用与舵机相同的 GB2312 编码加 `FFFF` 结束符；步数为单个 16 位 Word，不乘缩放。步数是软件剩余计数，没有编码器反馈。显示 VP 不再当作参数输入，避免文本回显改变下一次控制参数。连续启动不会重置正在执行的步数，写屏错误会记录并按桥接策略停止或重试；开机空闲不驱动电机或发送本场景显示。

**方向文字地址修复（2026-10-24）：** 方向文本改为 `STEPPER_DIRECTION=0x1300`，两个 GB2312 汉字和 `FFFF` 结束符占 `0x1300~0x1302`。步数仍为 `0x1063`，语音仍从 `0x1064` 开始。原方向 VP `0x1062` 的文本会覆盖步数和语音起点，造成文字尾部乱码。必须配套下载仓库根目录 `hmi/Smart_Experiment_Kit_stepper_text_fix` 的串口屏工程，方向控件 VP 为 `1300`、文本长度为 6 字节；仅更新开发板程序或重启服务不能更新串口屏内部工程。详见 [屏幕更新说明](../hmi/README.md)。

## 语音连续聆听修复（2026-10-17）

点语音开始后保持聆听，句末停顿只结束当前句，下一次讲话自动开启下一句；点停止才取消任务。讯飞网络收发使用独立线程，连接、发送限速和句末等待不会阻塞主采音线程。传输故障最多自动恢复三次，恢复期间屏幕显示提示；授权、额度和返回格式错误直接停止并显示原因，仍遵守原每日调用上限。连接持续过慢造成缓冲积压时会取消当前句、清理旧音频并提示重新说话，避免延迟越来越大或整个采音任务退出。正常等待下一句时保留上一次识别文字。

## 智能门禁录入与本地/SSH 电脑双端预览

人脸录入使用已确认的串口屏组合：`FACE_ENROLL_KEY_ADDR=0x3010`、`FACE_ENROLL_BACK_VALUE=0x5010`。收到正确帧后路由为 `access.face_enroll`，启动人脸录入任务。没有新增其他屏幕按钮或结果 VP。

运行 `bash run.sh` / `python main.py` 后，仅建立本机预览 HTTP 服务（默认 `http://127.0.0.1:8765/`），相机、Firefox 和云端调用均等待人脸按钮操作。点击录入后，一个采集线程读取开发板上的 USB 摄像头；开发板本地火狐与 SSH 电脑浏览器共享同一帧源，人脸检测同样读取这个共享相机，不会再开第二个 VideoCapture。

本地存在同一用户的图形桌面时，点录入后尝试打开 Firefox。SSH 电脑在主程序已启动后执行 `python camera_view.py 用户名@开发板IP`，会建立 SSH 显示通道并打开电脑浏览器；页面可以提前等待，点串口屏后自动出现开发板摄像头画面。SSH 电脑不需要连接摄像头。本地和远端都可同时观看，断开其中一端不影响另一端。

录入先预览约 3 秒供人员对准镜头，再通过讯飞检测单人、尺寸与清晰度，检查是否重复。确认新人员后，将 JPEG 照片、人员编号、姓名和录入时间事务写入开发板的 `data/faces_xfyun.sqlite3`；未指定姓名时自动生成“人员0001”等名称。写库成功才报告成功，重复人员不再新增。可用 `python main.py --list-faces` 查看，或 `--enroll-name 姓名` 设置下一次新录入姓名。

页面显示录入进度、失败原因及“已保存到本地库”的人员信息。完成、停止或异常后关闭摄像头并清除实时画面，页面保留本次结果；停止期间迟到的检测结果不会写库。连续重复点击不会创建重叠采集任务，摄像头未完全释放时禁止再次打开。讯飞录入仍需要有效授权和剩余免费次数；网页观看本身不额外消耗 API 次数。

身份验证使用 `IDENTITY_AUTHENTICATION_KEY_ADDR=0x3020`、`IDENTITY_AUTHENTICATION_BACK_VALUE=0x5020`。每次点击启动一次验证：当前人脸经讯飞检测后，与本地库中照片逐人调用讯飞比对，默认相似度至少 0.80、候选差值至少 0.05，并连续两轮确认同一人。成功后门锁 BOARD29 通电 2 秒后断电；不匹配、多人、空库、验证超时或接口失败时不开锁，蜂鸣器 BOARD40 鸣响 2 秒。没有人脸或画面质量不足时，在默认 20 秒验证时限内等待。任务结束关闭相机，网页显示验证结论，等待下一次按钮操作，不持续调用 API。两秒联动期间重复验证按钮被拒绝，避免反复触发或延长通电。验证失败蜂鸣由智能门禁场景直接控制和计时。距离检测单独根据距离控制蜂鸣器，停止检测直接关闭蜂鸣器。

配置位于 `experiments/access/jetson/config.py`：`CameraConfig.preview_enabled`、`preview_port`、`preview_local_browser`，以及 `FaceConfig.enroll_preview_seconds`。主入口支持 `--preview-port 8766`、`--no-local-browser`（保留电脑端预览）和 `--no-camera-preview`。

身份验证结果的 RGB 提示持续两秒：成功亮绿灯、失败亮红灯，到期释放门禁灯色状态。门锁和蜂鸣器仍分别按原有两秒计时关闭。

## 单独测试摄像头网页预览

下述独立入口保留用于调试，只打开相机和网页，不启动 GPIO、串口屏、人脸识别或任何云端 API，不消耗讯飞次数；未加入开机自启。正常通过串口屏录入时不需要运行它，也不要与主程序同时运行去抢占同一摄像头/预览端口。

开发板在 `ningbo/` 目录手动运行：

```bash
.venv/bin/python -B camera_preview.py
# 也可显式指定设备，或避免自动打开本地浏览器：
.venv/bin/python -B camera_preview.py --camera-device /dev/video0 --no-local-browser
```

有本地已登录的图形桌面和 Firefox 时，会自动请求火狐打开 `http://127.0.0.1:8765/`。通过 SSH 或 systemd 启动时，程序尝试查找**同一 Linux 用户**的本地桌面会话；桌面尚未就绪时每 10 秒重试。程序不会通过 SSH X11 转发把开发板 Firefox 错当作本地桌面打开，也不修改 `xhost`、显示服务器权限或用户身份。没有桌面时，摄像头和网页服务仍继续，日志输出查看地址。桌面用户与运行用户不同、Firefox 未安装或图形授权不可用时，需要在开发板桌面手动打开上述地址；实际 HDMI/Firefox 启动效果须在 Jetson 验证。

SSH 电脑可复制本项目的 `camera_view.py` 到电脑，在有 Python 3.8+ 和系统 SSH 客户端的终端执行（不需要 OpenCV 或额外 Python 依赖）：

```bash
python camera_view.py 用户名@开发板IP
```

它会建立 SSH 本地端口转发，验证预览服务后自动打开**这台电脑**的默认浏览器；SSH 密码或主机指纹确认仍在终端输入。保持该终端运行，按 Ctrl+C 断开远程观看；这不会停止开发板的采集或本地火狐画面。也可直接使用普通 SSH：

```bash
ssh -L 8765:127.0.0.1:8765 用户名@开发板IP
# 然后在 SSH 电脑的浏览器打开 http://127.0.0.1:8765/
```

电脑端口被占用时：`python camera_view.py 用户名@开发板IP --local-port 8766`，会打开 `http://127.0.0.1:8766/`。开发板预览端口可用 `camera_preview.py --port 8766` 修改，电脑端对应设置 `--remote-port 8766`。

本地和远程可同时显示，不根据“是否存在 SSH 连接”互相关闭。摄像头只有一个采集线程，多个浏览器共享最近一帧；关闭一个页面不影响其他页面。HTTP 只监听 `127.0.0.1`，无需把摄像头端口暴露到整个局域网。图像不写文件、不上传云端。页面显示连接、停止、断开及画面过期状态，停止或断开时清除旧画面。

开发板终端按 Ctrl+C 或发送 SIGTERM 会停止采集并释放摄像头、HTTP 端口。低层采集读取未返回时保留线程并拒绝重复打开同一实例。主程序已复用 `experiments/access/jetson/camera_stream.py` 与 `preview.py`；独立调试程序是另一个进程，不能和主程序同时占用同一相机。

验证入口：

```bash
python -B camera_preview.py --check-config
python -B -m unittest discover -s tests -p test_camera_preview.py -v
```

预览测试使用模拟摄像头和真实本机 HTTP，验证多客户端收流、摄像头单线程所有权、断开/停止清帧、阻塞读取保护、浏览器请求及 SSH 查看脚本；网页用模拟图像在浏览器核对过显示、停止和异常状态。没有把这些结果当成 Jetson USB/HDMI 实机验收。

实现参考：[Firefox 官方启动参数](https://firefox-source-docs.mozilla.org/browser/CommandLineParameters.html)、[Python HTTP 服务文档](https://docs.python.org/3/library/http.server.html)。

> **2026-10-17 启动 GPIO 更新：** 应用启动时，`DeviceManager.start()` 先初始化门锁、风扇、RGB 和蜂鸣器的静态输出，使用各驱动的关闭电平：BOARD29=低、BOARD31=低、BOARD12/35/38=低、BOARD40=高（蜂鸣器低有效）。初始化不会开锁、转风扇、亮灯或鸣响；步进四相 BOARD13/16/18/22 也在启动时设为输出低电平，只关闭线圈；舵机 PWM 和步进相序线程仍等待首次显式控制，正常传感器后台采样保持原设计。状态检测仍只由检测请求/串口屏按钮启动，尚未初始化的电机返回“未知”；应用就绪前丢弃串口屏探测及输入缓存。此修改只在应用运行后接管引脚，不改变上电至程序运行前的硬件默认电平，更新代码本身不安装或启用开机自启。
>
> 本次沿用已部署 GitHub 版本的静态输出关闭电平，没有反转极性或修改引脚编号。使用模拟 GPIO 检查初始化、空循环、按钮、检测及退出路径；真实模块是否关闭仍以接线及现场观察为准。当前开发板由 `ningbo-new.service` 运行 `/opt/ningbo`，已按用户要求开启自启；旧 `ningbo.service` 禁用并停止。随包 systemd 文件是通用模板，两套程序不可同时占用硬件。

> **2026-10-16 更新：人脸默认已改为讯飞云端 API。** 接入、免费试用额度配置、本机照片库和部署方式以 [FACE_API.md](FACE_API.md) 为准。当前默认无需人脸 ONNX、TensorRT 或 Whisper 权重；部署按所选后端安装依赖。新增 `.env.face.example`、`deploy/configure_face.py`、云端客户端/照片库/任务模块及离线测试。下方保留的是修改前源码包的完整清单与说明，其中固定文件数、默认本地人脸模型、旧人脸数据库、部署总是安装 GPU 依赖等描述仅反映原版，不能据此判断当前默认行为。

> **2026-10-17 温湿度采样更新：** 新结构沿用了旧版 DHT11 采样器的释放/重新申请引脚流程，存在相同的丢位风险。现已同步在旧服务及串口屏验证通过的原生采样器：保持 GPIO line handle，通过 `GPIOHANDLE_SET_CONFIG_IOCTL` 切换输入，并检查应答高脉冲持续时间，再读取完整 40 位及校验和。ARM64 可执行文件与源码一起更新，继续使用原有失败重试、范围检查及 BOARD37/gpiochip1/151 配置；不启动新版或启用自启动。采样成功会记录 `ningbo-dht11` 日志，运行时可用 `journalctl -t ningbo-dht11 -b` 查看。

## 目录、文件内容与调用关系说明

**对应源码包：** `NingBo_Minimal_NoOLED_20261016.zip`，即已移除 OLED 的精简版。目录清点结果为 **164 个文件、38 个子目录、9 个实验场景**；子目录数量不包含最外层 `ningbo/`。

这份 README 独立交付，可放到 `ningbo/README.md` 替换原来的简短说明。说明依据是当前压缩包内的实际实现，不补写尚不存在的功能；没有在本次修改业务源码、接线参数或屏幕地址。

## 一、先分清三部分代码

以风扇为例，`experiments/fan/jetson/` 负责风扇配置和 GPIO 控制，`experiments/fan/serial_screen/` 负责按钮地址及解码，`experiments/fan/bridge.py` 决定“收到启动按钮后调用哪个控制接口”。其余实验也按这个原则组织。

| 名称 | 在本工程中的含义 |
|---|---|
| `jetson/` | 本实验在开发板侧执行的代码；按实际设备可能使用 GPIO、I²C、UART、USB 或模型推理，不是所有实验都有 GPIO。 |
| `serial_screen/` | 本实验的 Python 屏幕通信代码，不是串口屏设计软件的工程文件。 |
| `bridge.py` | 本实验的桥接：把按钮、传感器结果、模型结果和硬件动作连接起来。 |
| `config.py` | 配置参数及校验；根目录负责汇总，各场景负责自己的具体默认值。 |
| `controller.py` | 场景硬件的初始化、采样/控制和关闭接口；其内部一般有一个 `Hardware` 类。 |
| `gpio.py` / `driver.py` | 对实际设备执行 GPIO 时序、串口或 I²C 协议的底层实现。 |
| `readings.py` | 把设备快照筛选、转换成页面和联动需要的值。 |
| `service.py` | 工作任务或对外 Python 接口；本工程的这个文件名不自动意味着 HTTP/网络服务。 |
| `__init__.py` | Python 包入口；多数只有说明，少数还导出类或定义注册表，下面逐个标明。 |

**几个必须按当前代码理解的边界：**九个场景共享同一套底层连接和应用生命周期，不是九个独立进程。部分传感器按钮只停止页面刷新与联动，采样线程仍运行。风扇页面没有数值写回；步进已增加状态写屏，但现有中文 VP 重叠尚未解决；门禁画面通过浏览器预览。语音默认使用讯飞云端，而不是默认离线。OLED 已不在这份目录中。

## 二、完整目录树

下面只列压缩包实际存在的目录和文件。模型、密钥、数据库及运行时生成目录另在第六节说明，未混入此树。

```text
ningbo/
├── deploy/
│   ├── 99-ningbo-i2c.rules
│   ├── 99-ningbo-uart.rules
│   ├── __init__.py
│   ├── configure_smoke_uart.sh
│   ├── configure_voice.py
│   ├── ningbo.service
│   ├── platform_check.py
│   └── verify_models.py
├── experiments/
│   ├── access/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── camera.py
│   │   │   ├── checks.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   ├── engine.py
│   │   │   ├── features.py
│   │   │   ├── library.py
│   │   │   ├── lock_control.py
│   │   │   ├── lock_gpio.py
│   │   │   ├── service.py
│   │   │   ├── tensorrt_models.py
│   │   │   └── tensorrt_runner.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── body_temperature/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   ├── driver.py
│   │   │   ├── feedback.py
│   │   │   └── readings.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── environment/
│   │   ├── jetson/
│   │   │   ├── native/
│   │   │   │   ├── dht11_gpiod
│   │   │   │   └── dht11_gpiod.c
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   ├── dht11.py
│   │   │   ├── light.py
│   │   │   └── readings.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── fan/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   └── gpio.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── servo/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   └── gpio.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   ├── config.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── smoke/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   ├── driver.py
│   │   │   ├── readings.py
│   │   │   └── smoke_protocol.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── stepper/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   └── gpio.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── ultrasonic/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── alarm.py
│   │   │   ├── buzzer_gpio.py
│   │   │   ├── config.py
│   │   │   ├── controller.py
│   │   │   ├── feedback.py
│   │   │   ├── gpio.py
│   │   │   └── readings.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── voice/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── checks.py
│   │   │   ├── config.py
│   │   │   ├── microphone.py
│   │   │   ├── recognizer.py
│   │   │   ├── service.py
│   │   │   ├── whisper_recognizer.py
│   │   │   └── xfyun_recognizer.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   └── __init__.py
├── hardware/
│   ├── adc/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── current.py
│   │   └── voltage.py
│   ├── rgb/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── controller.py
│   │   ├── feedback.py
│   │   └── gpio.py
│   ├── screen/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── frames.py
│   │   ├── sender.py
│   │   └── serial_discovery.py
│   ├── __init__.py
│   ├── gpio.py
│   ├── i2c.py
│   ├── pinmap.py
│   └── validation.py
├── system/
│   ├── diagnostics/
│   │   ├── jetson/
│   │   │   ├── __init__.py
│   │   │   ├── actuator_checks.py
│   │   │   ├── config.py
│   │   │   ├── definitions.py
│   │   │   ├── sensor_checks.py
│   │   │   └── service.py
│   │   ├── serial_screen/
│   │   │   ├── __init__.py
│   │   │   ├── addresses.py
│   │   │   └── screen.py
│   │   ├── __init__.py
│   │   └── bridge.py
│   ├── __init__.py
│   └── pin_test.py
├── .env.voice.example
├── application.py
├── config.py
├── constraints-jetson.txt
├── deploy.sh
├── device_manager.py
├── device_service.py
├── main.py
├── paths.py
├── README.md
├── requirements-whisper.txt
├── requirements.txt
├── run.sh
├── scene_router.py
├── screen_registry.py
└── screen_service.py
```

## 三、程序怎样运行

### 3.1 主入口与启动次序

```text
main.py
  -> 解析命令行，汇总并校验 Config
  -> NingBoApplication（application.py）
       -> 组装 DeviceService、ScreenService
       -> 组装 FaceService、VoiceService 与 ScreenDeviceBridge
       -> start()：先连接屏幕，再初始化设备和启动传感器采样
       -> run()：反复读取屏幕事件、调用对应桥接、刷新显示/联动
       -> stop()：停止桥接任务、停止输出、清理设备、关闭屏幕
```

这是当前源码的实际顺序，不是修复后的理想流程。`FaceService` 和 `VoiceService` 的对象会被创建，但摄像头识别、录入或麦克风采音在相应按钮触发后才开始，不是创建对象就立刻开始采集。

### 3.2 按钮怎样控制风扇

```text
串口屏回传按钮帧
  -> screen_service.py：读取字节
  -> hardware/screen/frames.py：拆帧
  -> screen_registry.py：调用对应场景解码
  -> experiments/fan/serial_screen/screen.py：得到 fan.start / fan.stop
  -> application.py：处理事件
  -> scene_router.py：找到风扇桥接
  -> experiments/fan/bridge.py：下发 on=True / False
  -> device_service.py -> device_manager.py
  -> experiments/fan/jetson/controller.py
  -> experiments/fan/jetson/gpio.py
  -> hardware/gpio.py -> Jetson.GPIO -> 风扇控制引脚
```

风扇动作在实物上观察；本例没有“测量转速并回传”的后半段。

### 3.3 温湿度怎样显示到串口屏

```text
experiments/environment/jetson/controller.py 的采样线程
  -> dht11.py / light.py：读取真实设备
  -> 更新 DeviceManager 中的 sensors 快照
  -> scene_router.py 的 tick() 取得快照
  -> experiments/environment/bridge.py
  -> experiments/environment/jetson/readings.py：字段整理与光照归一化
  -> screen_service.py：按 scene 选择格式化器
  -> experiments/environment/serial_screen/screen.py：缩放、校验、生成更新项
  -> hardware/screen/sender.py：构造写变量帧
  -> 唯一屏幕串口 -> 页面数值控件
```

串口屏控制停止后，环境桥接不再周期发送这些值，但默认的后台采样线程没有因此退出。体温、测距、烟雾的显示启停也要作同样区分。

### 3.4 门禁与语音为什么有更多文件

门禁的 `service.py` 要协调摄像头、模型、特征库、录入和连续识别，因此分别拆为 `camera.py`、`engine.py`、`library.py`、TensorRT 文件等。语音的 `service.py` 要协调麦克风和多个识别后端，因此拆为 `microphone.py`、`recognizer.py`、`xfyun_recognizer.py`、`whisper_recognizer.py`。它们不是重复的 GPIO 驱动。

## 四、每个文件夹与每个文件的详细说明

以下覆盖原包全部 164 个文件。每一项先说明用途，再说明里面的代码或配置内容；重要行为边界另外标注。文件路径均相对 ningbo/。

### 4.1 `ningbo/` - 项目根目录

ningbo/ 是整个项目文件夹；真正的 Python 主入口是 main.py。根目录负责启动、配置汇总、共享生命周期、场景路由、依赖与部署入口，具体实验动作位于 experiments/。

#### `README.md`

**用途：** 项目原有的简要入口说明，不是程序运行模块。

**里面包含：** 原文件包含九个实验目录表、基本启动方法、模型与语音密钥准备、部署要求和版本边界。当前这份详细说明可用于替换项目根目录原来的 README.md。

#### `main.py`

**用途：** 整个项目的正式命令行入口。正常启动时由它创建 NingBoApplication，再进入主循环。

**里面包含：** 包含参数解析、配置覆盖、日志设置、SIGINT/SIGTERM 退出信号处理；也提供配置检查、推理加速检查、音频/摄像头枚举、人脸库初始化、查询和改名等分支。

**注意与关联：** --check-config 只做配置结构校验，不证明实物接线或模型可用；--check-acceleration 会真正加载模型并执行前向，不能当成纯静态检查。

**主要类/函数：** `main`。

#### `application.py`

**用途：** 负责整台实训箱的生命周期和主循环，不负责具体传感器时序。

**里面包含：** NingBoApplication 组装 DeviceService、ScreenService、VoiceService、FaceService 和场景路由；start() 启动服务，process_once() 依次处理屏幕事件并刷新显示，run() 持续循环，stop() 清理识别、执行器和连接。

**注意与关联：** 当前源码的启动顺序仍是先串口屏、后设备；退出时先停止桥接，再 emergency_stop()。本说明如实记录现有顺序，不表示此前启动/退出安全问题已修复。

**主要类/函数：** `NingBoApplication`。

#### `config.py`

**用途：** 全局配置汇总入口，把各实验和共享硬件的配置对象组合成一份 Config。

**里面包含：** 包含 BOARD 编号方式、I²C 总线、保留的 I²C/UART 引脚、线程关闭超时，以及 dht11、light、fan、face、voice、screen 等配置字段；gpio_assignments() 汇总引脚，validate() 调用分散在各模块中的校验函数，DEFAULT_CONFIG 是默认实例。

**注意与关联：** 具体设备默认参数主要在所属实验的 jetson/config.py；不要只改这个文件就认为已经改了所有场景参数。

**主要类/函数：** `Config`。

#### `device_manager.py`

**用途：** 统一持有硬件连接、设备快照、锁和生命周期，并把实际操作分发给各实验自己的 Hardware 对象。

**里面包含：** 根据 experiments/__init__.py 的 HARDWARE_MODULES 建立硬件模块；管理初始化、后台采样线程、输出停止、读数快照、执行器控制、电流/电压监测以及引脚维护模式。

**注意与关联：** 这是共享资源和生命周期管理器，不是风扇、舵机等具体驱动的替代品；实际 GPIO 操作仍在对应场景驱动中。语音识别和人脸识别任务由各自 service.py 管理，不由这里启动。

**主要类/函数：** `DeviceManager`。

#### `device_service.py`

**用途：** 向桥接代码提供统一设备接口，避免业务层直接操作 GPIO 和 I²C。

**里面包含：** 封装 start/stop、snapshot、control、stop_actuator、emergency_stop、read_adc、同步/异步诊断和引脚测试；内部连接 DeviceManager 与 DiagnosticService，并检查部分操作是否已经启动设备服务。

**注意与关联：** 场景 bridge.py 通常先调用这里，再由管理器转到场景 controller.py 和具体驱动。

**主要类/函数：** `DeviceService`。

#### `scene_router.py`

**用途：** 接收标准屏幕事件，找到对应实验的 bridge.py；同时协调各场景的周期刷新。

**里面包含：** ScreenDeviceBridge 管理九个场景桥接、活动显示场景、门禁、语音、诊断、RGB 联动和近距离蜂鸣器；ControlScreenBridge 组合风扇、舵机、步进控制桥接。tick() 读取设备快照并调度各场景。

**注意与关联：** 这里仍保留跨场景协调。环境、体温、烟雾、测距的 active_scenes 控制的是刷新/联动，不等于逐一启停后台采样线程。

**主要类/函数：** `ScreenDeviceBridge`, `ControlScreenBridge`。

#### `screen_registry.py`

**用途：** 汇总每个场景自己的屏幕地址与事件定义，供底层帧解析和显示校验使用。

**里面包含：** 动态加载九个场景的 addresses.py、screen.py，合并 ADDRESS_MAP、BUTTON_EVENT_MAP、INPUT_EVENT_MAP；注册诊断按钮，检查部分重复注册；decode_event() 依次调用各场景解码函数，__getattr__() 转出已有命名常量。

**注意与关联：** 它不定义一套新的场景地址，地址来源仍是各目录下的 addresses.py。这里只做已有的部分冲突检查，不能理解为完整协议故障校验。

**主要类/函数：** `__getattr__`, `decode_event`。

#### `screen_service.py`

**用途：** 屏幕服务入口：接收业务消息、转交场景格式化、统一发送；反方向读取串口并解析事件。

**里面包含：** MessageParser 把 dict 或 JSON 字符串统一处理，支持 body_temp/body/smoke_alarm 等场景别名；ScreenService 组合 ScreenSender 与 ScreenEventParser，提供 handle_message() 和 read_screen_events()。

**注意与关联：** 这个类名里有 Service，但当前实现是 Python 对象，不是 HTTP 服务；没有网页接口或独立网络服务进程。

**主要类/函数：** `MessageParser`, `ScreenService`。

#### `paths.py`

**用途：** 统一计算项目根目录的绝对路径。

**里面包含：** 只定义 PROJECT_ROOT = Path(__file__).resolve().parent，供模型、数据库、密钥及本地采样程序路径拼接使用。

**注意与关联：** 它不是路径配置清单；各资源的具体子目录仍由相应模块配置。

#### `requirements.txt`

**用途：** 基础 Python 依赖声明。

**里面包含：** 列出 pyserial、Jetson.GPIO、smbus2、sounddevice、scipy、numpy、opencv-python-headless、websocket-client；备注 TensorRT 和 CUDA PyTorch 应使用与 JetPack 匹配的 NVIDIA 包。

**注意与关联：** 它没有包含全部语音后端：Whisper 另有安装步骤，Vosk 依赖也未在这里声明。不能把安装此文件等同于所有功能就绪。

#### `requirements-whisper.txt`

**用途：** Whisper 后端的辅助 Python 依赖清单。

**里面包含：** 声明 numba、tiktoken、tqdm、more-itertools 的版本范围；注释要求先准备 CUDA PyTorch，再单独以 --no-deps 安装指定版本的 openai-whisper。

**注意与关联：** 这个文件本身既不包含 Whisper 权重，也不直接声明 openai-whisper 安装项；对应安装动作在 deploy.sh。

#### `constraints-jetson.txt`

**用途：** 部署时配合 requirements 使用的版本约束文件。

**里面包含：** 对 numpy、opencv-python-headless、scipy 设置上界，并固定 websocket-client==1.8.0，用于约束该工程部署脚本的依赖组合。

**注意与关联：** 它不是独立安装清单，也不是已经成功部署的环境快照；这些范围来自包内配置，没有在本次说明中重新验证兼容性。

#### `.env.voice.example`

**用途：** 讯飞语音密钥配置模板。

**里面包含：** 只包含 XFYUN_APP_ID、XFYUN_API_KEY、XFYUN_API_SECRET 三个空值和填写提示；实际读取文件是项目根目录的 .env.voice。

**注意与关联：** 本文件不含真实密钥。不要把填写后的 .env.voice 当作普通源码随包公开；配置读取还允许环境变量覆盖文件值。

#### `run.sh`

**用途：** 使用项目 .venv 启动 main.py 的便捷脚本。

**里面包含：** 检查 .venv/bin/python 是否存在，设置 CUDA 动态库搜索路径、Python 输出与用户包选项，切换到项目根目录后执行 python -B main.py，并转发命令行参数。

**注意与关联：** 它不会自动创建环境或安装依赖；没有 .venv 时会退出并提示部署。直接运行会进入硬件应用，除非传入检查/查询参数。

#### `deploy.sh`

**用途：** 开发板部署总脚本，负责安装环境和执行部署检查。

**里面包含：** 包含 --plan、--check-only、--torch-wheel 参数；检查平台与三个模型哈希，安装系统库和 JetPack 组件，创建 .venv，安装或复用 CUDA PyTorch，安装 Whisper，设置设备组和 udev 权限，配置烟雾 UART，编译 DHT11 C 采样器，初始化人脸库并执行加速检查。

**注意与关联：** 无参数执行会联网、调用 sudo 并修改系统/项目环境，不应当作为阅读项目时的测试命令。即使默认语音是讯飞，脚本仍先校验 Whisper 权重并安装其依赖；--plan 也要求通过平台和模型检查。脚本不会自动安装启用 ningbo.service。

### 4.2 `ningbo/experiments/` - 九个实验的总目录

本目录按实验而不是按技术类型组织源码。每个实验都有 jetson/、serial_screen/ 和 bridge.py，但内部文件数量按真实功能不同，不强行给语音增加 GPIO 驱动，也不给没有回传功能的页面编造显示代码。

#### `experiments/__init__.py`

**用途：** 实验包的注册入口，供屏幕和硬件管理器发现各场景。

**里面包含：** EXPERIMENTS 列出九个实验；HARDWARE_MODULES 列出要创建 Hardware 对象的模块，包括八个实验的 controller.py 和共享 RGB controller.py。

**注意与关联：** 语音没有列入硬件控制器表，由 VoiceService 单独管理；此文件只有注册信息，不在导入时启动实际设备。

### 4.3 `ningbo/experiments/environment/` - 环境检测（温湿度、光照）

这是“环境检测（温湿度、光照）”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/environment/__init__.py`

**用途：** 将 `experiments/environment/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/environment/bridge.py`

**用途：** 把环境检测页的开始/停止按钮与温湿度、光照显示接起来。

**里面包含：** EnvironmentBridge 在开始时登记刷新任务，在 tick() 中读取 SnapshotReader 的结果并写屏；停止时移除任务并把三个显示值清零。

**注意与关联：** 本文件的 on_start()/on_stop() 目前只有占位说明，不会启停 DHT11/BH1750 采样线程；日志中的“采集已停止”不能按字面理解为硬件采样线程退出。

**主要类/函数：** `EnvironmentBridge`。

### 4.4 `ningbo/experiments/environment/jetson/` - 环境检测（温湿度、光照）：开发板侧代码

保存“环境检测（温湿度、光照）”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/environment/jetson/__init__.py`

**用途：** 将 `experiments/environment/jetson/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/environment/jetson/config.py`

**用途：** 保存 DHT11 和 BH1750 的默认参数与校验规则。

**里面包含：** Dht11Config 包含 BOARD 37、gpiod_native/python_poll 后端、原生程序路径、gpiochip/line、读取重试和时序；LightConfig 包含 I²C 地址 0x23、转换等待时间和显示归一化满量程 1000 lx；另有 validate() 与引脚登记函数。

**注意与关联：** 这里的 gpiochip1/151 是当前代码中的板级配置；换系统/载板时不能仅凭这一数值认定接线正确。

**主要类/函数：** `Dht11Config`, `LightConfig`, `validate`, `pin_assignments`。

#### `experiments/environment/jetson/controller.py`

**用途：** 环境场景的硬件管理模块，创建并管理 DHT11、BH1750 两路读取器。

**里面包含：** Hardware 负责驱动初始化、各自的后台采样线程、读取异常转状态、采样时间与快照更新、读数关闭；READER_CONFIG_NAMES 将 bh1750 读数名对应到 light 配置。

**注意与关联：** 它向 DeviceManager 提供生命周期接口，但采样逻辑代码就在本实验目录内；传感器启动后按各自 interval 采样。

**主要类/函数：** `Hardware`。

#### `experiments/environment/jetson/dht11.py`

**用途：** DHT11 温湿度读取的实际驱动。

**里面包含：** Dht11Driver 支持调用原生 gpiod 程序或 Python GPIO 时序读取；包含 40 位数据解析、校验和、温湿度范围检查、重试间隔、取消等待和短时间缓存。

**注意与关联：** read() 返回 temp/humidity 字段；使用默认原生后端时会通过 subprocess 调用 native/dht11_gpiod。

**主要类/函数：** `Dht11Driver`。

#### `experiments/environment/jetson/light.py`

**用途：** BH1750 光照传感器的实际 I²C 读取驱动。

**里面包含：** LightDriver 在共享 I²C 锁内发送上电和单次高分辨率测量命令，等待转换，读取两个字节并按 counts_per_lux 换算，返回 lux。

**注意与关联：** 这里返回的是 lx 数值；页面百分比换算不在此文件，而在 readings.py。

**主要类/函数：** `LightDriver`。

#### `experiments/environment/jetson/readings.py`

**用途：** 把环境传感器快照转换为环境页面需要的字段。

**里面包含：** SnapshotReader 将 temp 转为 temperature，保留 humidity，并用 lux / display_full_scale_lux × 100 计算光照显示值，限制在 0～100。

**注意与关联：** 该百分比是项目设定的显示归一化，不是传感器直接测得的“光照百分比”；本文件没有体温/测距读取器那样的完整过期数据过滤。

**主要类/函数：** `SnapshotReader`。

### 4.5 `ningbo/experiments/environment/jetson/native/` - DHT11 原生采样程序

存放同一套 DHT11 采样程序的 C 源码和 ARM64 可执行文件。默认 Python 驱动通过子进程调用这里的可执行文件；源码用于部署时重新编译。

#### `experiments/environment/jetson/native/dht11_gpiod`

**用途：** 已编译的 DHT11 原生采样可执行文件，由 dht11.py 调用。

**里面包含：** 文件检查结果为 Linux ARM aarch64、64 位 ELF 动态链接程序；接受 gpiochip 路径和 line offset，正常输出两个数值。

**注意与关联：** 这是无扩展名的二进制，不是多余文本文件；默认 gpiod_native 后端依赖它。不能在 Windows 当作 Python 脚本执行，部署时可由同目录 C 源码重新编译。

#### `experiments/environment/jetson/native/dht11_gpiod.c`

**用途：** DHT11 原生采样辅助程序的 C 源码。

**里面包含：** 使用 Linux GPIO 字符设备接口申请指定 gpiochip 和 line，拉低启动信号后保持同一个 handle 切换输入，校验应答脉冲时长、读取完整 40 位数据并检查校验和。成功时标准输出只打印温度和湿度两个数值，并通过 syslog 记录有效读数；失败返回非零退出码。兼容 Ubuntu 20.04 较旧头文件，运行内核需支持当前 Jetson Linux 5.10 提供的 `GPIOHANDLE_SET_CONFIG_IOCTL`。

**注意与关联：** deploy.sh 使用 gcc 编译此文件；它不是 Python 模块，通常不用从 PyCharm 直接运行。

### 4.6 `ningbo/experiments/environment/serial_screen/` - 环境检测（温湿度、光照）：串口屏通信代码

保存“环境检测（温湿度、光照）”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/environment/serial_screen/__init__.py`

**用途：** 将 `experiments/environment/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/environment/serial_screen/addresses.py`

**用途：** 环境检测页的变量地址和按钮定义。

**里面包含：** 定义温度 0x1000、湿度 0x1010、光照 0x1020；定义开始/停止按钮地址与返回值，以及 ADDRESS_MAP、BUTTON_EVENT_MAP、INPUT_EVENT_MAP。

**注意与关联：** 按钮识别使用“地址＋返回值”组合；这里的 INPUT_EVENT_MAP 为空，当前没有环境页数值输入事件。

#### `experiments/environment/serial_screen/screen.py`

**用途：** 环境页的按钮解码和显示数值格式化。

**里面包含：** decode_event() 识别本页按钮；make_update() 检查有限数字、四舍五入及显示范围；build_updates() 将温度/湿度乘 100、光照乘 1，形成地址与数值更新项。

**注意与关联：** 它只构造更新数据，不直接打开串口；实际写帧由统一 ScreenSender 完成。

**主要类/函数：** `make_update`, `decode_event`, `build_updates`。

### 4.7 `ningbo/experiments/body_temperature/` - 人体温度检测

这是“人体温度检测”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/body_temperature/__init__.py`

**用途：** 将 `experiments/body_temperature/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/body_temperature/bridge.py`

**用途：** 连接人体温度页按钮、温度快照和 RGB 场景联动。

**里面包含：** BodyTemperatureBridge 处理开始/停止、登记定时刷新、调用本场景 SnapshotReader；开始时检查是否有真实驱动，停止时清零温度显示并释放本场景 RGB 结果。

**注意与关联：** 这里不直接访问 I²C，也不会单独关闭后台体温采样线程。

**主要类/函数：** `BodyTemperatureBridge`。

### 4.8 `ningbo/experiments/body_temperature/jetson/` - 人体温度检测：开发板侧代码

保存“人体温度检测”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/body_temperature/jetson/__init__.py`

**用途：** 将 `experiments/body_temperature/jetson/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/body_temperature/jetson/config.py`

**用途：** 保存 GY-906/MLX90614 参数及本项目的体温演示规则。

**里面包含：** 包含型号 mlx90614、地址 0x5A、采样间隔、目标/环境/显示温度范围；表面测温演示规则为低于 25℃ 蓝灯、25～28℃ 绿灯、超过 28℃ 红灯；有效温度超过 28℃ 时每秒短鸣一次（响 0.2 秒、停 0.8 秒）。停止采集、降温或读数失效后解除测温蜂鸣请求。有效判定上限为传感器量程 380℃。这些是教学演示阈值，不是医学发热判断。

**注意与关联：** 阈值和范围是代码中的演示配置，不是本次验证过的测温校准结果。

**主要类/函数：** `BodyTemperatureConfig`, `validate`。

#### `experiments/body_temperature/jetson/controller.py`

**用途：** 人体温度场景的驱动装配和采样管理。

**里面包含：** 默认创建 Mlx90614Driver，也允许显式注入真实驱动；包含启用状态检查、后台采样、错误和时间戳写入、关闭流程。

**注意与关联：** 传感器快照同时保存 temperature 与 ambient_temperature；屏幕页当前只显示目标温度。

**主要类/函数：** `Hardware`。

#### `experiments/body_temperature/jetson/driver.py`

**用途：** 通过共享 I²C 读取 MLX90614 两个温度寄存器。

**里面包含：** smbus_pec() 计算 CRC-8；Mlx90614Driver 读取环境寄存器 0x06、目标寄存器 0x07，检查响应长度、PEC、错误位与量程，并换算为摄氏度。

**注意与关联：** 只读温度，不修改 EEPROM 或发射率；代码返回的是目标表面温度，不能把字段名自动理解为已校准的医学体温。

**主要类/函数：** `smbus_pec`, `Mlx90614Driver`。

#### `experiments/body_temperature/jetson/feedback.py`

**用途：** 定义体温场景自己的 RGB 颜色判断。

**里面包含：** color_for() 复用 valid_number() 检查读数，有效温度高于配置阈值返回 red，否则 green；无有效读数返回等待颜色。

**注意与关联：** 这里只决定本场景应提出什么颜色，最终写哪种颜色仍由 hardware/rgb/feedback.py 汇总多个场景后决定。

**主要类/函数：** `color_for`。

#### `experiments/body_temperature/jetson/readings.py`

**用途：** 从设备快照筛出可用于体温页面显示的数据。

**里面包含：** SnapshotReader 使用 display_range 和读数有效期选取 temperature；valid_number() 排除错误/禁用状态、布尔值、非有限值、超时和越界值。

**注意与关联：** 无有效值时返回空字典，不伪造新读数；bridge.py 因而可能不刷新该次显示。

**主要类/函数：** `SnapshotReader`, `valid_number`。

### 4.9 `ningbo/experiments/body_temperature/serial_screen/` - 人体温度检测：串口屏通信代码

保存“人体温度检测”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/body_temperature/serial_screen/__init__.py`

**用途：** 将 `experiments/body_temperature/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/body_temperature/serial_screen/addresses.py`

**用途：** 人体温度页的显示与按钮地址。

**里面包含：** 定义 BODY_TEMPERATURE=0x1030、开始/停止按钮地址及返回值；ADDRESS_MAP 使用 body.temperature，按钮事件使用 body_temperature.start/stop。

**注意与关联：** 保留源码中的两套命名，不要把显示字段名和路由场景名混为一谈。

#### `experiments/body_temperature/serial_screen/screen.py`

**用途：** 人体温度页的按钮解析和温度编码。

**里面包含：** 将本页按钮映射为标准事件；将 temperature 乘 100，按整数四舍五入，校验到 0～32767 后生成写屏更新项。

**注意与关联：** 屏幕小数显示位需与这个缩放约定一致；本文件不负责读取温度硬件。

**主要类/函数：** `make_update`, `decode_event`, `build_updates`。

### 4.10 `ningbo/experiments/ultrasonic/` - 超声波测距与近距报警

这是“超声波测距与近距报警”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/ultrasonic/__init__.py`

**用途：** 将 `experiments/ultrasonic/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/ultrasonic/bridge.py`

**用途：** 连接测距按钮、距离显示、RGB 和近距离蜂鸣器。

**里面包含：** UltrasonicBridge 开始时登记显示并 activate() 近距离报警；停止时 release() 报警并立即尝试关闭蜂鸣器，同时清零距离显示；tick() 发送最新有效距离。

**注意与关联：** 开始/停止控制本场景的显示与联动；后台超声波读取线程由 controller.py 管理。

**主要类/函数：** `UltrasonicBridge`。

### 4.11 `ningbo/experiments/ultrasonic/jetson/` - 超声波测距与近距报警：开发板侧代码

保存“超声波测距与近距报警”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/ultrasonic/jetson/__init__.py`

**用途：** 将 `experiments/ultrasonic/jetson/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/ultrasonic/jetson/alarm.py`

**用途：** 把有效距离变化转换为蜂鸣器启停请求。

**里面包含：** ProximityAlarm 管理激活、释放、读数观察和输出刷新；只有场景激活、配置允许且有效距离低于危险阈值时才请求鸣响，输出失败后保留后续重试机会。

**注意与关联：** 蜂鸣器判断与 RGB 颜色协调相互独立；并非 RGB 亮红灯就一律鸣响。

**主要类/函数：** `ProximityAlarm`。

#### `experiments/ultrasonic/jetson/buzzer_gpio.py`

**用途：** 蜂鸣器的实际 GPIO 开关驱动。

**里面包含：** BuzzerDriver 把逻辑 on/off 按 active_high 转成引脚电平，提供 start/set/stop/snapshot，并在 GPIO 写成功后才更新内部状态。

**注意与关联：** 当前是开关控制，不是音调合成或语音播放。

**主要类/函数：** `BuzzerDriver`。

#### `experiments/ultrasonic/jetson/config.py`

**用途：** 超声波和蜂鸣器的接线、采样时序及距离阈值配置。

**里面包含：** UltrasonicConfig 默认 TRIG=BOARD 32、ECHO=33，包含超时、声速、最大距离、是否近距鸣响；BuzzerConfig 默认 BOARD 40、低电平有效；危险/提醒阈值常量为 5 cm 和 8 cm。

**注意与关联：** 同一文件登记超声波两根信号线和蜂鸣器引脚，并校验距离阈值次序。

**主要类/函数：** `UltrasonicConfig`, `BuzzerConfig`, `validate`, `pin_assignments`。

#### `experiments/ultrasonic/jetson/controller.py`

**用途：** 同时管理本场景的超声波输入和蜂鸣器输出。

**里面包含：** Hardware 创建 UltrasonicDriver 与 BuzzerDriver，管理测距后台线程、读数快照、蜂鸣器初始化/控制/关闭，以及输出诊断信息。

**注意与关联：** 它比纯传感器场景多了执行器接口；蜂鸣器虽然是输出设备，代码归属于超声波实验。

**主要类/函数：** `Hardware`。

#### `experiments/ultrasonic/jetson/feedback.py`

**用途：** 测距场景的 RGB 规则。

**里面包含：** color_for() 使用有效期和量程过滤距离：低于危险阈值给 red，处于提醒区间给等待颜色，达到安全区间给 green。

**注意与关联：** 默认区间对应小于 5 cm 红、5～8 cm 蓝、至少 8 cm 绿；这些是项目配置，不是底层测距算法。

**主要类/函数：** `color_for`。

#### `experiments/ultrasonic/jetson/gpio.py`

**用途：** 超声波 TRIG/ECHO 的实际 GPIO 测距驱动。

**里面包含：** UltrasonicDriver 初始化输出/输入，发送触发脉冲，等待 ECHO 上升和下降，依据高电平持续时间换算厘米，并检查边沿超时、最短脉宽和最大距离。

**注意与关联：** read() 返回 distance；不负责决定是否报警或如何显示。

**主要类/函数：** `UltrasonicDriver`。

#### `experiments/ultrasonic/jetson/readings.py`

**用途：** 过滤并提取用于测距显示与联动的距离。

**里面包含：** SnapshotReader 读取 ultrasonic 快照；valid_number() 检查状态、数值类型、有限性、时间戳、有效期和量程。

**注意与关联：** 返回 distance 字段或空字典；既被显示桥接使用，也被蜂鸣器/RGB 规则复用。

**主要类/函数：** `SnapshotReader`, `valid_number`。

### 4.12 `ningbo/experiments/ultrasonic/serial_screen/` - 超声波测距与近距报警：串口屏通信代码

保存“超声波测距与近距报警”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/ultrasonic/serial_screen/__init__.py`

**用途：** 将 `experiments/ultrasonic/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/ultrasonic/serial_screen/addresses.py`

**用途：** 超声波页面的距离显示和启停按钮定义。

**里面包含：** 距离变量为 0x1040；另包含测距开始/停止按钮地址、返回值以及三张注册映射。

**注意与关联：** INPUT_EVENT_MAP 为空；距离是开发板发给屏幕的显示结果，不是当前页的用户输入。

#### `experiments/ultrasonic/serial_screen/screen.py`

**用途：** 测距页按钮解码和距离写屏格式转换。

**里面包含：** 解析本页事件，将厘米数乘 10 后生成整数更新项，保留一位小数的传输约定；校验非有限值及 Word 显示范围。

**注意与关联：** 实际采样和蜂鸣器启停都不在此文件。

**主要类/函数：** `make_update`, `decode_event`, `build_updates`。

### 4.13 `ningbo/experiments/smoke/` - 烟雾读数场景

这是“烟雾读数场景”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/smoke/__init__.py`

**用途：** 将 `experiments/smoke/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/smoke/bridge.py`

**用途：** 烟雾页面的启停与读数刷新桥接。

**里面包含：** SmokeBridge 在激活后读取本场景 SnapshotReader，定时将 value 写屏；停止时移除刷新任务并清零。

**注意与关联：** 尽管场景名称叫烟雾报警，当前 on_start/on_stop 没有额外动作，本文件没有实现烟雾阈值触发蜂鸣器或风扇的逻辑。

**主要类/函数：** `SmokeBridge`。

### 4.14 `ningbo/experiments/smoke/jetson/` - 烟雾读数场景：开发板侧代码

保存“烟雾读数场景”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/smoke/jetson/__init__.py`

**用途：** 将 `experiments/smoke/jetson/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/smoke/jetson/config.py`

**用途：** 烟雾模块的 UART 参数与协议设置。

**里面包含：** 默认 /dev/ttyTHS0、9600、8N1、ffaa 协议；包含读超时、采样间隔、缓冲大小、数据过期时间，并校验 raw/ffaa 两种模式。

**注意与关联：** 这是烟雾专用串口参数，不是串口屏的 115200 波特率配置。

**主要类/函数：** `SmokeConfig`, `validate`。

#### `experiments/smoke/jetson/controller.py`

**用途：** 烟雾驱动装配、采样线程及状态快照管理。

**里面包含：** 创建 SmokeDriver，可注入自定义 decoder；持续采样并保存 online、raw、percent、concentration 等字段；对 ffaa 模式保留驱动的 normal/unknown/error 状态，对未解码字节标为未知或未配置。

**注意与关联：** 这里不把“收到串口字节”直接视为已经取得有效浓度。

**主要类/函数：** `Hardware`。

#### `experiments/smoke/jetson/driver.py`

**用途：** 打开烟雾 UART 并把串口数据交给协议解析器。

**里面包含：** SmokeDriver 管理独占串口连接、分段读取、最新有效浓度及时间戳、数据过期、异常关闭与重新打开；默认使用 SmokeFrameParser，也支持显式注入返回 0～100 的百分比解码器。

**注意与关联：** 默认 ffaa 结果 unit=raw、percent=None；当前源码没有自动将原始浓度校准为百分比。

**主要类/函数：** `SmokeDriver`。

#### `experiments/smoke/jetson/readings.py`

**用途：** 决定烟雾快照中哪些数值可以写到页面。

**里面包含：** ffaa 模式验证浓度与有效期，超过 32767 的值记录警告但不截断发送；其他模式只在 decoder 已给出 percent 时才发送百分比显示值。

**注意与关联：** 不能因为串口屏背景图写了 %，就认定这个读取器已经把默认原始浓度换算为百分比。

**主要类/函数：** `SnapshotReader`, `valid_number`。

#### `experiments/smoke/jetson/smoke_protocol.py`

**用途：** 解析烟雾模块 FF AA H L 3C 格式的数据流。

**里面包含：** SmokeFrameParser 缓存碎片、寻找帧头、检查尾字节、合并高低字节为原始浓度，并统计有效帧、尾部错误、丢弃字节和总接收量。

**注意与关联：** 该协议实现没有校验和，只有帧格式检查；文件注释明确说明浓度单位尚未标定。

**主要类/函数：** `SmokeFrameParser`。

### 4.15 `ningbo/experiments/smoke/serial_screen/` - 烟雾读数场景：串口屏通信代码

保存“烟雾读数场景”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/smoke/serial_screen/__init__.py`

**用途：** 将 `experiments/smoke/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/smoke/serial_screen/addresses.py`

**用途：** 烟雾页面的显示和按钮地址表。

**里面包含：** 定义 SMOKE_VALUE=0x1050，烟雾开始/停止按钮地址及返回值，注册 smoke.value 与 smoke.start/stop。

**注意与关联：** 这里不定义烟雾阈值、单位或 UART 协议。

#### `experiments/smoke/serial_screen/screen.py`

**用途：** 烟雾页按钮解析和数值更新格式化。

**里面包含：** decode_event() 匹配本页按钮；build_updates() 对 value 采用 1 倍缩放，生成写屏数据并检查有效数字与显示范围。

**注意与关联：** 函数只处理数值，不自动添加或校准百分比单位。

**主要类/函数：** `make_update`, `decode_event`, `build_updates`。

### 4.16 `ningbo/experiments/fan/` - 风扇控制

这是“风扇控制”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/fan/__init__.py`

**用途：** 将 `experiments/fan/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/fan/bridge.py`

**用途：** 把风扇页的开始/停止按钮直接变为风扇开关命令。

**里面包含：** FanScreenBridge 收到 fan.start 时调用 devices.control('fan', on=True)，收到 fan.stop 时传 on=False，并返回处理结果。

**注意与关联：** 不回写风扇转速或运行数值，返回 sent=[]；运行效果由实物观察。

**主要类/函数：** `FanScreenBridge`。

### 4.17 `ningbo/experiments/fan/jetson/` - 风扇控制：开发板侧代码

保存“风扇控制”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/fan/jetson/__init__.py`

**用途：** 将 `experiments/fan/jetson/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/fan/jetson/config.py`

**用途：** 风扇引脚与电平极性配置。

**里面包含：** FanConfig 默认 BOARD 31、高电平有效、enabled=True；pin_assignments() 把启用的风扇引脚交给全局冲突检查。

**注意与关联：** 修改风扇引脚首先看这里，而不是 hardware/gpio.py。

**主要类/函数：** `FanConfig`, `pin_assignments`。

#### `experiments/fan/jetson/controller.py`

**用途：** 管理风扇驱动实例及其输出状态。

**里面包含：** Hardware 创建 FanDriver，负责初始化、control/stop_actuator、停止关闭、快照更新和 GPIO 输出读回；正常控制前会检查设备已启动、没有进入引脚维护模式。

**注意与关联：** 驱动具体电平写入仍在同目录 gpio.py；这里负责把通用设备接口接到本场景。

**主要类/函数：** `Hardware`。

#### `experiments/fan/jetson/gpio.py`

**用途：** 包含完整的风扇 GPIO 开关实现。

**里面包含：** FanDriver 初始化为关闭电平，按 active_high 转换逻辑状态，提供 set(on)、stop()、snapshot() 和线程锁；只有成功写 GPIO 后才更新 on。

**注意与关联：** snapshot() 返回的是软件保存的开关状态，不是转速传感器测量值。

**主要类/函数：** `FanDriver`。

### 4.18 `ningbo/experiments/fan/serial_screen/` - 风扇控制：串口屏通信代码

保存“风扇控制”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/fan/serial_screen/__init__.py`

**用途：** 将 `experiments/fan/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/fan/serial_screen/addresses.py`

**用途：** 风扇启动、停止按钮的地址与返回值。

**里面包含：** 定义 fan.start/fan.stop 的 BUTTON_EVENT_MAP；ADDRESS_MAP 和 INPUT_EVENT_MAP 为空。

**注意与关联：** 当前没有风扇速度、百分比或其他回传显示地址。

#### `experiments/fan/serial_screen/screen.py`

**用途：** 只解码风扇按钮，不构造数值显示。

**里面包含：** decode_event() 返回标准按钮事件；FIELDS 为空，调用 build_updates() 会明确抛出没有显示字段的 ValueError。

**注意与关联：** 这是当前设计边界，不要把这个文件误当成尚未填完的转速显示功能。

**主要类/函数：** `decode_event`, `build_updates`。

### 4.19 `ningbo/experiments/access/` - 人脸门禁

这是“人脸门禁”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/access/__init__.py`

**用途：** 将 `experiments/access/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/access/bridge.py`

**用途：** 门禁场景的屏幕事件、识别结果、RGB 和门锁联动中心。

**里面包含：** AccessScreenBridge 处理 access.start、access.stop、access.face_enroll；使用会话编号过滤过期识别结果，在主循环 tick() 读取 FaceService 队列，更新指示灯并调用 AccessLockControl；包含任务错误、录入指示时长和停止清理。

**注意与关联：** 返回 accepted 表示命令受理，不等于识别成功；只有明确的识别结论才会参与联动。此文件没有向屏幕传摄像头图像。

**主要类/函数：** `AccessScreenBridge`。

### 4.20 `ningbo/experiments/access/jetson/` - 人脸门禁：开发板侧代码

保存“人脸门禁”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/access/jetson/__init__.py`

**用途：** 门禁开发板代码包的导出入口。

**里面包含：** 导入 FaceService，并通过 __all__ 对外导出，允许从本包直接引用该服务。

**注意与关联：** 原注释残留 Sensor/sensors 路径，当前包内没有该旧目录；实际摄像头代码在同目录 camera.py。

#### `experiments/access/jetson/camera.py`

**用途：** USB 摄像头设备发现、打开、取帧和释放。

**里面包含：** camera_nodes() 枚举 Linux 视频节点并查询 V4L2 能力；UsbCamera 自动选择 USB 图像采集节点或使用指定设备，通过 OpenCV 设置尺寸/FPS、验证首帧、持续 read()；多台设备时要求明确选择。

**注意与关联：** 当前是 USB/本地摄像头方案，不含 RTSP 拉流、网页视频服务或串口屏视频传输。这里保存首帧只是供识别使用。

**主要类/函数：** `_capabilities`, `camera_nodes`, `UsbCamera`。

#### `experiments/access/jetson/checks.py`

**用途：** 人脸模型的实际前向检查入口。

**里面包含：** check_acceleration() 要求选择 TensorRT，加载检测器与识别器，对零图像执行检测和特征计算，并返回后端、精度、DLA 或错误信息。

**注意与关联：** 不打开摄像头和 GPIO，但会加载模型、使用 GPU/DLA，可能触发引擎构建及缓存写入；由 main.py --check-acceleration 调用。

**主要类/函数：** `check_acceleration`。

#### `experiments/access/jetson/config.py`

**用途：** 保存摄像头、人脸模型、识别/录入策略和门锁参数。

**里面包含：** CameraConfig 默认自动找 USB 摄像头、请求 640×480/15 FPS；FaceConfig 包含 TensorRT/FP16、CUDA/DLA、ONNX 路径、引擎缓存、128 维特征、相似度与清晰度阈值、连续确认、录入样本数、SQLite 路径和开锁时长；LockConfig 默认 BOARD 29、高电平有效。

**注意与关联：** 当前 unlock_on_match 默认 True，unlock_seconds=2.0，验证成功才限时开锁；failure_buzzer_seconds=2.0。文件也校验摄像头路径只使用本地设备，不接受网络 URL。

**主要类/函数：** `CameraConfig`, `_face_model_path`, `_face_database_path`, `FaceConfig`, `LockConfig`, `validate`, `pin_assignments`。

#### `experiments/access/jetson/controller.py`

**用途：** 门禁场景中门锁输出的硬件管理模块。

**里面包含：** Hardware 创建 LockDriver，处理初始化、开关命令、停止关闭、输出快照、GPIO 读回及诊断状态。

**注意与关联：** 它只管理门锁执行器；人脸识别和摄像头任务由 service.py 管理，不能因为同叫 controller 就把两者混在一起。

**主要类/函数：** `Hardware`。

#### `experiments/access/jetson/engine.py`

**用途：** 将一帧图像变为可用于人脸比对的特征。

**里面包含：** FaceEngine 加载 YuNet/SFace，按配置选择 TensorRT 或显式 OpenCV CPU 调试后端；检查 BGR 图像、缩放、限制单人、检查脸部尺寸和清晰度、对齐人脸并输出归一化特征。

**注意与关联：** analyze() 返回是否有效、人数、提示及特征，不直接开锁，也不负责把人员信息写入数据库。

**主要类/函数：** `FaceEngine`。

#### `experiments/access/jetson/features.py`

**用途：** 统一人脸特征格式并为模型计算指纹。

**里面包含：** normalized_feature() 检查 128 维有限非零向量并作归一化；model_signature() 检查两个 ONNX 文件并计算 SHA256，构造与特征格式绑定的签名。

**注意与关联：** 模型指纹用于防止用不兼容模型读取旧特征库；文件缺失时会报错，不会自动下载或生成模型。

**主要类/函数：** `normalized_feature`, `model_signature`。

#### `experiments/access/jetson/library.py`

**用途：** 管理 SQLite 人脸库与特征模板匹配。

**里面包含：** FaceLibrary 创建 metadata、people、templates 表，检查数据库身份、版本与模型指纹，支持多样本录入、列人和改名；match_templates() 按人员合并模板分数，再检查相似度阈值和候选差距。

**注意与关联：** 默认保存姓名、时间和特征向量，不是录像文件库；本源码包没有附带旧人员数据库。

**主要类/函数：** `match_templates`, `FaceLibrary`。

#### `experiments/access/jetson/lock_control.py`

**用途：** 把确认识别结果转换为限时门锁输出。

**里面包含：** AccessLockControl 只在 unlock_on_match 开启时处理成功结果，设置开锁期限；tick() 到期关闭，失败/取消时撤销，关闭失败则保留请求以便后续重试。

**注意与关联：** 这层决定“何时开/关、维持多久”，lock_gpio.py 才决定“写哪个电平”。

**主要类/函数：** `AccessLockControl`。

#### `experiments/access/jetson/lock_gpio.py`

**用途：** 门锁继电器的实际 GPIO 开关驱动。

**里面包含：** LockDriver 根据 active_high 设置初始关闭电平，提供 set(on)、stop()、snapshot()；GPIO 写入成功后才更新软件状态。

**注意与关联：** on 表示驱动输出的逻辑状态；具体通电是否开锁取决于实际继电器和锁接线，不能从变量名推断所有实物行为。

**主要类/函数：** `LockDriver`。

#### `experiments/access/jetson/service.py`

**用途：** 人脸识别与录入的后台任务管理器。

**里面包含：** FaceService 组装 UsbCamera、FaceEngine、FaceLibrary；识别和录入共用单个工作任务，包含会话取消、结果队列、连续匹配确认、多帧采样一致性、重复人员检查、写库和摄像头/模型释放。

**注意与关联：** 默认录入采集 5 份有效特征、识别连续确认 3 次，这些来自配置；stop() 会等待工作线程，当前并非完全无等待的停止接口。

**主要类/函数：** `FaceService`。

#### `experiments/access/jetson/tensorrt_models.py`

**用途：** 将 TensorRT 前向输出适配成人脸检测和特征提取接口。

**里面包含：** decode_yunet() 解码多尺度框/关键点并做 NMS；align_face() 根据五点求变换并裁成 112×112；TensorRTDetector 处理固定尺寸补边，TensorRTRecognizer 构造 SFace 输入并取出特征。

**注意与关联：** 这里同时有 CPU 图像处理与 GPU/DLA 模型调用；“模型走 TensorRT”不等于整条前后处理链都在 GPU 上。

**主要类/函数：** `decode_yunet`, `align_face`, `TensorRTDetector`, `TensorRTRecognizer`。

#### `experiments/access/jetson/tensorrt_runner.py`

**用途：** 底层 TensorRT 8.x 固定输入推理与 CUDA 缓冲管理。

**里面包含：** CudaRuntime 通过 ctypes 调 CUDA Runtime，负责设备选择、显存分配释放、拷贝和同步；TensorRTRunner 解析 ONNX、按 FP16/FP32 与 DLA 配置构建/缓存引擎，分配绑定缓冲并 execute_v2()。

**注意与关联：** 当前实现明确检查 TensorRT 主版本 8，不会在失败时悄悄切到 CPU；缓存键包含模型、形状、运行环境和硬件配置，引擎不是通用跨设备模型文件。

**主要类/函数：** `CudaRuntime`, `TensorRTRunner`。

### 4.21 `ningbo/experiments/access/serial_screen/` - 人脸门禁：串口屏通信代码

保存“人脸门禁”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/access/serial_screen/__init__.py`

**用途：** 将 `experiments/access/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/access/serial_screen/addresses.py`

**用途：** 门禁页面的三个按钮定义。

**里面包含：** 注册人脸录入 access.face_enroll、启动 access.start、停止 access.stop 的地址与返回值；ADDRESS_MAP 和 INPUT_EVENT_MAP 为空。

**注意与关联：** 没有姓名、相似度或摄像头画面的输出地址。

#### `experiments/access/serial_screen/screen.py`

**用途：** 只解析门禁按钮事件。

**里面包含：** decode_event() 匹配上述三个按钮；FIELDS 为空，build_updates() 会报没有显示字段的错误。

**注意与关联：** 此版本没有门禁文字/图像写回功能，不能将 serial_screen 文件夹的存在等同于已接入视频窗口。

**主要类/函数：** `decode_event`, `build_updates`。

### 4.22 `ningbo/experiments/servo/` - 舵机控制

这是“舵机控制”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/servo/__init__.py`

**用途：** 将 `experiments/servo/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/servo/bridge.py`

**用途：** 处理舵机启停按钮，并把目标角度与指令方向写到屏幕。

**里面包含：** ServoScreenBridge 开始时调用 devices.control('servo', sweep=True)，登记显示状态；tick() 发现角度变化后刷新；停止时停 PWM 并显示“停止”和角度 0。

**注意与关联：** 方向由目标角度增减推断，不是方向传感器反馈；停止显示 0 也不表示舵机已实际归零。若启动时写屏失败，代码会尝试停止舵机。

**主要类/函数：** `ServoScreenBridge`。

### 4.23 `ningbo/experiments/servo/jetson/` - 舵机控制：开发板侧代码

保存“舵机控制”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/servo/jetson/__init__.py`

**用途：** 将 `experiments/servo/jetson/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/servo/jetson/config.py`

**用途：** 舵机 GPIO、硬件 PWM、角度范围和循环动作参数。

**里面包含：** 默认 BOARD 15、50 Hz、500～2500 μs 脉宽、0～180°角度范围；循环位置配置为 45°、135°、90°，每个位置停留 4 秒，显示刷新间隔 0.2 秒；包含相应参数校验。

**注意与关联：** 这些是程序下发参数，不是实际角度校准值。

**主要类/函数：** `ServoConfig`, `validate`, `pin_assignments`。

#### `experiments/servo/jetson/controller.py`

**用途：** 创建并管理 ServoDriver，把设备服务的控制接口接入舵机。

**里面包含：** Hardware 包含初始化、控制命令转发、单独停止、关闭、状态更新、诊断记录和输出接口；实例化驱动时传入线程退出超时。

**注意与关联：** 具体 PWM 与角度更新线程在 gpio.py；本文件不是第二份舵机底层时序实现。

**主要类/函数：** `Hardware`。

#### `experiments/servo/jetson/gpio.py`

**用途：** 舵机的硬件 PWM 驱动与循环角度调度。

**里面包含：** ServoDriver 通过共享 GpioResource 申请 PWM，将角度换成脉宽和占空比；独立线程按配置轮换目标位置，提供 set/stop/close/snapshot，并读取内核 PWM period、duty_cycle、enable。

**注意与关联：** 代码不是用 Python 循环翻转 GPIO 来模拟 PWM。快照中的 angle 是指令目标，内核 PWM 状态也不能证明舵机的实际机械角度。

**主要类/函数：** `ServoDriver`。

### 4.24 `ningbo/experiments/servo/serial_screen/` - 舵机控制：串口屏通信代码

保存“舵机控制”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/servo/serial_screen/__init__.py`

**用途：** 将 `experiments/servo/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/servo/serial_screen/addresses.py`

**用途：** 舵机页面的方向文本、角度与启停按钮地址。

**里面包含：** 方向文本地址为 0x10F0，目标角度为 0x1061；另定义 servo.start/stop 的键值组合。注释约定角度乘 10，方向文本使用 3 Word 空间。

**注意与关联：** INPUT_EVENT_MAP 为空，当前页没有被注册的用户角度输入事件；不要将目标角度显示地址理解为已经接入可调角度输入。

#### `experiments/servo/serial_screen/config.py`

**用途：** 保存方向选择控件的数值约定。

**里面包含：** ScreenControlConfig 定义 forward_value=0、reverse_value=1，并检查它们是不同的合法 16 位 Word；根 config.py 将其汇总为 screen_control。

**注意与关联：** 虽然文件放在 servo/serial_screen 下，当前实际读取这两个方向值的是 stepper/bridge.py。舵机显示的“正转/反转/停止”则在自己的 screen.py 中按文字编码。

**主要类/函数：** `ScreenControlConfig`, `validate`。

#### `experiments/servo/serial_screen/screen.py`

**用途：** 把舵机显示内容编码为屏幕可写的数据。

**里面包含：** 按钮按地址/返回值解码；target_angle 乘 10 转整数；direction 只允许正转、反转、停止，使用 GB2312 编码并追加 FF FF 结束标记。

**注意与关联：** 数值和文本在同一场景文件处理，但由统一串口发送器真正写出。

**主要类/函数：** `make_update`, `decode_event`, `build_updates`。

### 4.25 `ningbo/experiments/stepper/` - 步进电机控制

这是“步进电机控制”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/stepper/__init__.py`

**用途：** 将 `experiments/stepper/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/stepper/bridge.py`

**用途：** 接收步进方向和步数，校验后处理启动/停止。

**里面包含：** StepperScreenBridge 缓存 stepper.direction 与 stepper.steps；开始时映射为 fwd/rev 并调用驱动，缺少屏幕初值时采用默认正转、最多 512 步，速度取 default_speed_ms；停止时调用 stop_actuator。

**注意与关联：** 兼容内部控制参数的方向数值来自 config.screen_control；方向显示生成中文，剩余步数由主循环定时写回，停止写“停止、0”。现有中文地址重叠见文首。

**主要类/函数：** `StepperScreenBridge`。

### 4.26 `ningbo/experiments/stepper/jetson/` - 步进电机控制：开发板侧代码

保存“步进电机控制”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/stepper/jetson/__init__.py`

**用途：** 将 `experiments/stepper/jetson/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/stepper/jetson/config.py`

**用途：** 步进电机四相 GPIO 和步进节拍配置。

**里面包含：** 默认引脚为 BOARD 13、16、18、22；包含八拍相序表、默认 8 ms 步间隔、2～20 ms 允许范围、最大步数和空闲等待时间，并校验相序及引脚数量。

**注意与关联：** spd/速度参数在当前驱动中是每步等待的毫秒数，不是转速 rpm；数值越小意味着调度间隔越短。

**主要类/函数：** `StepperConfig`, `validate`, `pin_assignments`。

#### `experiments/stepper/jetson/controller.py`

**用途：** 管理 StepperDriver 生命周期并接收上层控制请求。

**里面包含：** Hardware 负责四相驱动创建、初始化、启动/停止命令、关闭线程、状态快照、诊断记录和四个引脚的输出读回接口。

**注意与关联：** 真实相序推进不在这个文件，而在 gpio.py 的 step_once() 与工作线程。

**主要类/函数：** `Hardware`。

#### `experiments/stepper/jetson/gpio.py`

**用途：** 步进电机四相输出的实际驱动。

**里面包含：** StepperDriver 根据 fwd/rev 增减相序索引，在线程中按毫秒间隔执行 step_once()；递减剩余步数，结束或停止时将四相拉为 0，记录线程错误与当前运行状态。

**注意与关联：** 快照的 n 是软件计数的剩余步数，不是编码器读取到的真实位移；close() 会请求停止并等待线程结束。

**主要类/函数：** `StepperDriver`。

### 4.27 `ningbo/experiments/stepper/serial_screen/` - 步进电机控制：串口屏通信代码

保存“步进电机控制”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/stepper/serial_screen/__init__.py`

**用途：** 将 `experiments/stepper/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/stepper/serial_screen/addresses.py`

**用途：** 步进页面的方向显示、步数显示和启停按钮定义。

**里面包含：** STEPPER_DIRECTION=0x1300、STEPPER_NUMBER=0x1063；方向占用三个独立 Word，两者为显示 VP，INPUT_EVENT_MAP 为空，按钮注册 stepper.start/stop。串口屏工程必须使用配套的新方向地址。

**注意与关联：** 它与风扇/舵机不同，确实包含当前运行代码会接收的数值输入事件。

#### `experiments/stepper/serial_screen/screen.py`

**用途：** 解析步进页面的按钮与参数回传。

**里面包含：** decode_event() 匹配按钮事件；build_updates() 按舵机写法生成 GB2312 方向文本，校验并生成 16 位数值步数，同时检查中文缓冲区与其他已注册显示 VP 的重叠。

**注意与关联：** 有参数输入并不等于有运行结果回传；本版本没有这里的数值写回逻辑。

**主要类/函数：** `decode_event`, `build_updates`。

### 4.28 `ningbo/experiments/voice/` - 语音识别与文字显示

这是“语音识别与文字显示”的场景目录。顶层 bridge.py 连接按钮事件和业务动作；jetson/ 保存本场景的开发板侧实现；serial_screen/ 保存本场景的屏幕地址与消息处理。各子目录和文件在下面分别说明。

#### `experiments/voice/__init__.py`

**用途：** 将 `experiments/voice/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/voice/bridge.py`

**用途：** 把语音页启停按钮与后台聆听、识别文本写屏连接起来。

**里面包含：** VoiceScreenBridge 保存会话编号、待发送结果和最后文本；开始/停止时清空文本，tick() 读取当前会话的新结果，过滤重复，串口失败时保留重试，编码错误时跳过该条。

**注意与关联：** 它只把语音转成显示文字，没有把识别句子解析为风扇、舵机等设备控制命令。

**主要类/函数：** `VoiceScreenBridge`。

### 4.29 `ningbo/experiments/voice/jetson/` - 语音识别与文字显示：开发板侧代码

保存“语音识别与文字显示”在开发板侧的实际实现，包括本场景需要的配置、硬件驱动、读取/控制或识别服务。不是一块额外的开发板，也不是另一个必须单独启动的项目；通常由根目录入口组装调用。

#### `experiments/voice/jetson/__init__.py`

**用途：** 语音开发板代码包的导出入口。

**里面包含：** 导入 VoiceService，并通过 __all__ 对外导出。

**注意与关联：** 原注释的 Sensor/sensors 是旧结构名称，不是本版实际目录；麦克风输入在同目录 microphone.py，且当前默认语音为云端讯飞，并非默认本地识别。

#### `experiments/voice/jetson/checks.py`

**用途：** 语音后端的推理检查接口。

**里面包含：** Whisper 模式下实际对零音频执行 CUDA transcribe 并报告设备；Vosk 不通过 GPU 检查；讯飞模式则直接返回 skipped=True 的说明。

**注意与关联：** 讯飞分支返回 ok=True 只是无需本地 GPU 检查，不验证网络、密钥、额度或真实识别效果。

**主要类/函数：** `check_acceleration`。

#### `experiments/voice/jetson/config.py`

**用途：** 语音输入、识别后端、讯飞连接和屏幕文字参数。

**里面包含：** VoiceConfig 默认 backend='xfyun'，自动选择 USB 麦克风；包含增益、采音缓冲/队列、静音分句、Whisper 路径、CUDA、讯飞密钥/计数库、连接与句末超时，以及 GB2312、32 Word 文字空间。

**注意与关联：** 本项目设置的讯飞每日上限为 450 次；注释中关于平台免费额度的说法没有在本次重新核验，不能据此保证免费。这里还校验后端、数值范围和超时。

**主要类/函数：** `_voice_model_path`, `VoiceConfig`, `validate`。

#### `experiments/voice/jetson/microphone.py`

**用途：** 负责选择 USB 音频输入并取得 PCM 音频块。

**里面包含：** 提供输入/输出设备列表和选择函数；UsbMicrophone 支持编号、名称、hw: 声卡路径及采音节点，自动选择优先 AIMIC-M4/AISPEECH，使用 sounddevice 采集单声道 int16，管理有界队列、溢出错误、读取与关闭。

**注意与关联：** 即使代码能枚举输出设备，也没有实现语音播报/TTS；队列满会记录错误，而不是无限缓存。

**主要类/函数：** `audio_inputs`, `audio_outputs`, `select_audio_device`, `UsbMicrophone`。

#### `experiments/voice/jetson/recognizer.py`

**用途：** 根据配置选择具体语音识别后端，并包含 Vosk 适配。

**里面包含：** RecognizerFactory 显式路由到 xfyun、whisper_cuda 或 vosk；VoskRecognizerFactory 缓存本地目录模型，VoskRecognizer 将 Vosk 结果统一为 text/final 字典。

**注意与关联：** 不会在 CUDA 出错时自动切到 Vosk。当前 requirements 没有声明 Vosk 包，默认模型路径也不是 Vosk 目录，启用前需另外准备。

**主要类/函数：** `VoskRecognizer`, `VoskRecognizerFactory`, `RecognizerFactory`。

#### `experiments/voice/jetson/service.py`

**用途：** 语音场景的后台工作线程和结果队列管理。

**里面包含：** VoiceService 组合 UsbMicrophone 与 RecognizerFactory，控制准备、采音、识别循环、临时结果节流、最终结果、会话隔离、错误快照和停止资源释放。

**注意与关联：** 与传感器场景不同，voice/jetson 下没有 Hardware/controller.py；语音由 application.py 直接创建的 VoiceService 管理。stop() 会关闭采音并等待线程。

**主要类/函数：** `VoiceService`。

#### `experiments/voice/jetson/whisper_recognizer.py`

**用途：** 本地 CUDA Whisper 后端。

**里面包含：** WhisperRecognizerFactory 检查 CUDA 与本地 .pt 文件并缓存 GPU 模型；WhisperRecognizer 对 PCM 增益/限幅、按静音分句、必要时重采样到 16 kHz，再执行 transcribe 并过滤无讲话结果。

**注意与关联：** 模型文件必须本地提供，CUDA 不可用时直接失败；这部分没有自动 CPU 降级。

**主要类/函数：** `WhisperRecognizerFactory`, `WhisperRecognizer`。

#### `experiments/voice/jetson/xfyun_recognizer.py`

**用途：** 默认讯飞流式语音后端的完整实现。

**里面包含：** 包含密钥文件/环境变量读取、HMAC 签名 URL、本地 SQLite 每日计数、音频增益与连续滤波降采样、动态修正结果拼接、WebSocket 收发线程、发送节拍、静音结束和会话取消。

**注意与关联：** 需要外网和有效授权；本地计数限制不是平台账单保证。当前发送节拍代码仍在此文件，已有延迟问题不因目录整理而自动消失。

**主要类/函数：** `credentials`, `signed_url`, `DailyQuota`, `PcmEncoder`, `ResultAssembler`, `XfyunRecognizer`。

### 4.30 `ningbo/experiments/voice/serial_screen/` - 语音识别与文字显示：串口屏通信代码

保存“语音识别与文字显示”的 Python 屏幕通信逻辑：addresses.py 定义地址和按钮，screen.py 负责本场景解码与可用的显示编码。这里不含屏幕编辑器原工程、背景图片、字库下载文件或屏幕固件。

#### `experiments/voice/serial_screen/__init__.py`

**用途：** 将 `experiments/voice/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `experiments/voice/serial_screen/addresses.py`

**用途：** 语音识别页面的文字 VP 与启停按钮定义。

**里面包含：** VOICE_RECOGNIZED_TEXT=0x1064；注册 voice.start/stop 两个按钮，以及 voice.recognized_text 显示字段。

**注意与关联：** 文字缓冲长度由 VoiceConfig.text_words 决定，不只占用一个 Word。

#### `experiments/voice/serial_screen/screen.py`

**用途：** 语音按钮解析和识别文字编码。

**里面包含：** voice_text_update() 检查编码、缓冲区长度、控制字符和 VP 冲突，按完整字符截断，做偶数字节对齐，追加 FF FF 并补齐缓冲；build_updates() 处理 recognized_text 字段。

**注意与关联：** 默认 32 Word 即 64 Byte，包含结束标记；这是字节容量而不是固定能显示 64 个汉字。不能编码的字符会报错，不是任意 Unicode 都可显示。

**主要类/函数：** `decode_event`, `voice_text_update`, `build_updates`。

### 4.31 `ningbo/hardware/` - 共享实物与底层连接

存放唯一 GPIO/I²C/屏幕串口的连接管理，以及多个场景共用的 RGB 和 ADC。这里没有 OLED 目录。共享的是实际连接或实物，不是把所有实验的具体控制重新塞到一个文件。

#### `hardware/__init__.py`

**用途：** 将 `hardware/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `hardware/gpio.py`

**用途：** 整块开发板共用的 Jetson.GPIO 连接与资源管理。

**里面包含：** GpioResource 延迟导入 Jetson.GPIO，设置 BOARD 编号，封装输入/输出配置、读写、硬件 PWM 申请/释放、内核 PWM 状态读取，并记录本项目占用的引脚用于 cleanup。

**注意与关联：** 不包含风扇、门锁或舵机的场景动作规则。读取内核 PWM 状态不是测量实际输出波形或机械角度。

**主要类/函数：** `GpioResource`。

#### `hardware/i2c.py`

**用途：** 整台设备共用的 I²C 总线连接及互斥锁。

**里面包含：** I2cResource 按需打开 smbus2.SMBus，提供原始字节读取和寄存器写入后 repeated START 读取，使用 RLock 避免多个传感器/ADC 同时打断总线事务。

**注意与关联：** BH1750、MLX90614 与 ADS1115 等复用此连接，但各自协议仍写在所属驱动中。

**主要类/函数：** `I2cResource`。

#### `hardware/pinmap.py`

**用途：** 从现有配置生成 40 针排针说明数据。

**里面包含：** board_pinmap() 合并场景 GPIO 登记、I²C/UART、供电和地，给每个物理针脚产生 pin、label、kind、side 字段，供设备快照/说明使用。

**注意与关联：** 不控制电平，也不配置设备树 pinmux；它只是根据软件配置生成的引脚表，不是对真实接线的探测。

**主要类/函数：** `board_pinmap`。

#### `hardware/validation.py`

**用途：** 进行跨场景连接配置的基础校验。

**里面包含：** 检查线程超时、BOARD 模式、允许 GPIO 引脚、重复占用、电源/地/总线冲突、烟雾与屏幕端口冲突以及部分 I²C 地址范围和重复。

**注意与关联：** 当前 I²C 检查实际只覆盖光照、部分电流地址和默认 ADS1115 地址，没有完整覆盖体温及所有反馈地址。不能把 validate() 通过理解为所有配置冲突都已排除。

**主要类/函数：** `validate`。

### 4.32 `ningbo/hardware/adc/` - 共享 ADS1115 电压与电流采集

保存 ADC 配置、原始电压读取和分流电阻电流换算。主要被 DeviceManager 和状态检测模块使用，不是第十个实验场景。

#### `hardware/adc/__init__.py`

**用途：** 将 `hardware/adc/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `hardware/adc/config.py`

**用途：** 集中保存这组共享 ADS1115 的电压采集、电流换算和执行器反馈映射。

**里面包含：** 定义 CurrentChannel、CurrentConfig、Ads1115Config，包含地址/通道、分流电阻、电流阈值、量程、超时、反馈地址和反馈电压阈值；默认电压检测开启、电流检测关闭。

**注意与关联：** 电流通道表和电压反馈表当前不是同一套映射，这是源码现状。改接线或启用电流前要核对两套表，不能直接假定一一对应。

**主要类/函数：** `CurrentChannel`, `_current_channels`, `Ads1115Config`, `CurrentConfig`, `validate`。

#### `hardware/adc/current.py`

**用途：** 基于 ADS1115 和配置的分流电阻换算电流。

**里面包含：** CurrentMonitor 根据设备名取 CurrentChannel，调用电压监测器读取指定地址/通道，拒绝量程饱和，再按 I=V/R 转为 mA。

**注意与关联：** 电流功能默认关闭，未启用时 read_ma() 会报错；没有自动识别分流电阻或校准映射的功能。

**主要类/函数：** `CurrentMonitor`。

#### `hardware/adc/voltage.py`

**用途：** ADS1115 四路单端电压采样实现。

**里面包含：** Ads1115Monitor 按通道/地址/量程配置单次转换，轮询 OS 位等待完成，校验配置读回，读取带符号原始值并换算电压，返回饱和标记和时间戳；read_all() 读取默认地址四路。

**注意与关联：** 电压是测量值，不会在这里直接判定风扇转没转；反馈阈值比较在 system/diagnostics/jetson/actuator_checks.py。

**主要类/函数：** `Ads1115Monitor`。

### 4.33 `ningbo/hardware/rgb/` - 共享 RGB 指示灯

保存 RGB 的引脚配置、实际输出、生命周期适配，以及体温/距离/门禁/录入同时使用一盏灯时的颜色协调。

#### `hardware/rgb/__init__.py`

**用途：** 将 `hardware/rgb/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `hardware/rgb/config.py`

**用途：** RGB 三路引脚以及跨场景反馈策略的配置。

**里面包含：** RgbConfig 默认红/绿/蓝对应 BOARD 12/35/38、高电平有效；RgbFeedbackConfig 汇总体温与距离阈值、读数有效期和等待颜色，另有引脚登记及校验。

**注意与关联：** 具体体温、距离颜色判断位于各自实验的 feedback.py；这里给协调器提供策略参数。

**主要类/函数：** `RgbConfig`, `RgbFeedbackConfig`, `validate`, `pin_assignments`。

#### `hardware/rgb/controller.py`

**用途：** 共享 RGB 指示灯的硬件管理模块。

**里面包含：** Hardware 创建 RgbDriver，向 DeviceManager 提供初始化、控制、停止关闭、状态快照和诊断登记接口。

**注意与关联：** 代码还保留一个通用样式的 actuator_output_levels()，实际读取 config.pin；但 RgbConfig 使用 pins 元组，这是当前实现的不一致，不能写成已经完整实现了 RGB 三路读回。

**主要类/函数：** `Hardware`。

#### `hardware/rgb/feedback.py`

**用途：** 多个实验共用同一个 RGB 灯时的颜色协调器。

**里面包含：** RgbFeedback 接收体温、测距、门禁、录入四类来源；体温/测距调用各实验自己的 color_for()，门禁/录入使用识别结果；按红色、等待颜色、绿色、关闭的优先级决定输出，并避免重复写灯。

**注意与关联：** environment 和 smoke 的 activate() 调用不在其 SOURCES 中，因此不会凭这两个调用自动形成环境/烟雾 RGB 报警。

**主要类/函数：** `RgbFeedback`。

#### `hardware/rgb/gpio.py`

**用途：** RGB 指示灯的三路实际 GPIO 输出。

**里面包含：** RgbDriver 定义 off/red/green/blue/yellow/cyan/magenta/white 的电平组合，支持颜色名或 r/g/b 开关，按极性逐路写入；部分写入失败时尝试关闭所有通道。

**注意与关联：** 它只落实最终颜色，不决定体温、距离与门禁之间谁优先。

**主要类/函数：** `RgbDriver`。

### 4.34 `ningbo/hardware/screen/` - 共享屏幕串口与帧协议

管理全项目唯一的屏幕串口、自动发现、5A A5 帧解析和写帧发送；各页面的按钮含义及显示字段仍在各场景 serial_screen/ 中。

#### `hardware/screen/__init__.py`

**用途：** 将 `hardware/screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `hardware/screen/config.py`

**用途：** 串口屏连接、轮询与刷新时间的配置。

**里面包含：** ScreenConfig 默认 port='auto'、115200、8N1，保存读写超时、刷新周期、事件轮询间隔、自动探测超时和重试次数；validate() 检查这些参数。

**注意与关联：** 各页面的变量地址不在这里，而在 experiments/*/serial_screen/addresses.py。

**主要类/函数：** `ScreenConfig`, `validate`。

#### `hardware/screen/frames.py`

**用途：** 对串口屏返回的字节流做基础帧拆分。

**里面包含：** ScreenEventParser 缓存分片，查找 5A A5 帧头，按长度提取帧，支持 0x83 读回和 0x82 写回中的 Word 数据，最终调用 screen_registry.decode_event()。

**注意与关联：** 这是底层帧解析，不是风扇等场景按钮定义；当前按长度等待完整帧的逻辑仍在这里，不能据此认定损坏帧恢复问题已修复。

**主要类/函数：** `ScreenEventParser`。

#### `hardware/screen/sender.py`

**用途：** 打开唯一的屏幕串口，并把更新项封装成 JGUS 写变量帧发送。

**里面包含：** ScreenSender 支持手动端口或自动发现，构造单 Word 和多 Word 字节帧，检查地址/长度/短写，发送并返回十六进制记录；也保存自动探测期间收到的待处理输入。

**注意与关联：** 文件顶部仍提及旧名字 screen_sender.py/message_parser.py，那是旧注释，不是本包还需要存在的额外文件；当前消息解析入口在 screen_service.py。

**主要类/函数：** `ScreenSender`。

#### `hardware/screen/serial_discovery.py`

**用途：** 自动寻找真正有屏幕协议应答的 USB 串口。

**里面包含：** discover_screen() 枚举候选端口、排除烟雾等已配置串口，通过读取 0x1000 和 0x1030 两个 VP 确认应答，保存探测期间其他字节；零块或多块应答时给出错误。

**注意与关联：** 自动探测不是只看 ttyUSB 编号，也不是纯枚举：它会打开候选 USB 串口并发送读变量请求。

**主要类/函数：** `_reply`, `discover_screen`。

### 4.35 `ningbo/system/` - 系统维护与状态检测

本目录是项目自身的维护功能，不是 Linux 系统源码。包含显式 GPIO 引脚维护和屏幕状态检测模块；这些都是运行功能，不是历史测试报告。

#### `system/__init__.py`

**用途：** 将 `system/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `system/pin_test.py`

**用途：** 人工维护时的引脚输出测试工具。

**里面包含：** PinTest 提供进入/退出维护模式、设置允许引脚电平、查看测试状态、清理其他输出；进入前请求停止执行器并等待正在采样的任务，维护期间普通场景控制受限制。

**注意与关联：** 这里的 test 是硬件维护功能，不是已删除的 tests 单元测试目录。调用会改变真实 GPIO 输出，不应在正常实验同时随意执行。

**主要类/函数：** `PinTest`。

### 4.36 `ningbo/system/diagnostics/` - 状态检测功能

为现有状态检测页服务，同样分为 bridge.py、jetson/ 和 serial_screen/。它跨多个实验检查状态，但不作为九个实验之外的新教学场景计数。

#### `system/diagnostics/__init__.py`

**用途：** 将 `system/diagnostics/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `system/diagnostics/bridge.py`

**用途：** 将状态检测页按钮转为诊断任务，并把变化的结果写回屏幕。

**里面包含：** DiagnosticsScreenBridge 根据 diagnostics.all/sensors/actuators 或单项名调用 request_diagnostics()，tick() 比较报告 revision，只把已有显示地址的状态和元信息交给屏幕服务。

**注意与关联：** 屏幕调用的是后台诊断请求；具体诊断仍可能与设备控制锁/采样共享资源，不代表完全不会影响主控制。

**主要类/函数：** `DiagnosticsScreenBridge`。

### 4.37 `ningbo/system/diagnostics/jetson/` - 状态检测执行与判断

包含诊断配置、设备分组、任务线程、输入检查和输出电气反馈检查。该目录不是自动修复模块，检测范围受已接入的反馈硬件与配置约束。

#### `system/diagnostics/jetson/__init__.py`

**用途：** 状态检测实现包的公共导出入口。

**里面包含：** 导入并通过 __all__ 导出 DiagnosticService、SensorStatusChecker、ActuatorStatusChecker，供 device_service.py 等模块使用。

#### `system/diagnostics/jetson/actuator_checks.py`

**用途：** 评估执行器当前驱动、输出信号及电气反馈是否与命令一致。

**里面包含：** ActuatorStatusChecker 检查控制错误/线程状态、静态 GPIO 或舵机内核 PWM，再多次读取电流或 ADC 电压并取中位数，根据已配置阈值给出 normal/error/idle/unknown/unconfigured。

**注意与关联：** 检测范围标为 electrical，不验证实际角度、转速或机械动作；缺阈值时不会硬判正常。当前整个 check() 仍持有控制锁，采样等待不代表与停止操作完全隔离。

**主要类/函数：** `ActuatorStatusChecker`。

#### `system/diagnostics/jetson/config.py`

**用途：** 执行器反馈诊断的采样策略配置。

**里面包含：** ActuatorDiagnosticConfig 默认采 3 次、间隔 0.02 秒、开启 GPIO 读回；validate() 校验采样次数、间隔及估计耗时与线程关闭超时的关系。

**注意与关联：** 这不是各传感器的日常采样频率配置，也不是 ADC 的通道映射。

**主要类/函数：** `ActuatorDiagnosticConfig`, `validate`。

#### `system/diagnostics/jetson/definitions.py`

**用途：** 诊断目标、别名和整体状态汇总规则。

**里面包含：** 列出六类传感器/采样模块与六类执行器，包含 light/body_temp/ads1115 别名；overall_status() 按 error、checking、unconfigured、unknown、idle、normal、disabled 的顺序选汇总状态。

**注意与关联：** current 是后台诊断目标之一，但当前状态页没有给它独立的结果 VP。

**主要类/函数：** `overall_status`。

#### `system/diagnostics/jetson/sensor_checks.py`

**用途：** 检查传感器读取是否得到有效数据。

**里面包含：** SensorStatusChecker 对温湿度、光照、测距、体温和烟雾做字段/范围/协议状态检查；current 未启用时可读取 ADS1115 电压但返回电流换算尚未配置；启用时调用 CurrentMonitor。

**注意与关联：** 当前 _check_current_monitor() 按 I²C 地址去重，只取每个地址的一个通道，不能说明已经检测全部配置通道。此处的 normal 也不等于每个输入都已标定准确。

**主要类/函数：** `SensorStatusChecker`。

#### `system/diagnostics/jetson/service.py`

**用途：** 状态检测的同步接口和后台任务调度。

**里面包含：** DiagnosticService 选择单项/分组目标，调用 SensorStatusChecker 或 ActuatorStatusChecker；管理单个诊断线程、取消事件、busy、progress、revision、results 和 overall。

**注意与关联：** request() 返回任务是否受理，不是最终检测结果；snapshot() 才读取不断更新的报告，stop() 会等待诊断线程结束。

**主要类/函数：** `DiagnosticService`。

### 4.38 `ningbo/system/diagnostics/serial_screen/` - 状态检测页的地址和编码

保存诊断按钮、各设备状态 VP、状态数值编码、地址冲突检查及写屏转换。进度等未配置 VP 的字段只留在程序内部。

#### `system/diagnostics/serial_screen/__init__.py`

**用途：** 将 `system/diagnostics/serial_screen/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `system/diagnostics/serial_screen/addresses.py`

**用途：** 状态检测页的按钮、结果 VP 和状态码。

**里面包含：** 定义全检/传感器/执行器三个按钮，11 项状态结果地址 0x6000～0x6009 与 0x6010，及 unknown=0、normal=1 等状态编码；保存 STATUS_CHECK_BINDINGS、STATUS_RESULT_ADDRESS_MAP、STATUS_META_ADDRESS_MAP。

**注意与关联：** busy、progress、overall 的显示地址当前均为 None；内部有进度/汇总数据，但这版页面没有对应输出控件地址。

#### `system/diagnostics/serial_screen/screen.py`

**用途：** 诊断按钮注册校验及状态结果写屏编码。

**里面包含：** configured_status_button_events() 注册完整按钮键值，validate_status_result_addresses() 检查结果地址与已有变量/按钮是否冲突；build_updates() 将状态名转状态码，并处理进度等元信息。

**注意与关联：** 地址为 None 的显示项会跳过；不要为它们凭空写一个 VP。该模块通过 screen_registry 参与注册，没有九个实验 screen.py 那样的 decode_event()。

**主要类/函数：** `make_update`, `configured_status_button_events`, `validate_status_result_addresses`, `build_updates`。

### 4.39 `ningbo/deploy/` - 开发板安装与配置辅助文件

存放平台检查、模型哈希检查、密钥填写、I²C/UART 权限规则、烟雾串口配置和 systemd 服务模板。它不是平时各场景执行动作的目录，也不是可以整体视作无用测试的目录。

#### `deploy/__init__.py`

**用途：** 将 `deploy/` 标记为 Python 包，支撑本工程的包路径导入。

**里面包含：** 当前文件只有包说明字符串，没有设备初始化、业务控制、采样线程或独立启动逻辑。它不能代替同目录的驱动/桥接文件；本次只解释其作用，没有删除或改写。

#### `deploy/99-ningbo-i2c.rules`

**用途：** I²C 设备节点的 udev 权限规则。

**里面包含：** 把 i2c-dev 子系统设备设置为 i2c 组、0660 权限，供组内普通用户访问。

**注意与关联：** 规则需要由部署脚本安装到系统规则目录并重载后才生效，不会因为文件放在项目中自动生效。

#### `deploy/99-ningbo-uart.rules`

**用途：** 烟雾传感器所用 ttyTHS0 的 udev 权限规则。

**里面包含：** 匹配 tty 子系统的 ttyTHS0 节点，设置 dialout 组和 0660 权限；注释指出串口登录服务需要另行禁用。

**注意与关联：** 它针对烟雾 UART，不是串口屏自动扫描规则。

#### `deploy/configure_smoke_uart.sh`

**用途：** 为当前配置中的烟雾 UART 设置系统权限并释放串口登录占用。

**里面包含：** 先用项目 .venv 读取 DEFAULT_CONFIG，只有启用烟雾且端口为 /dev/ttyTHS0 才继续；安装 UART 规则，执行 systemctl disable --now nvgetty.service，重载 udev，再调整 ttyTHS0 属组及权限。

**注意与关联：** 该脚本会修改系统服务和设备权限。它不是烟雾读数驱动，也不是模拟工具。

#### `deploy/configure_voice.py`

**用途：** 交互式填写讯飞密钥的命令行工具。

**里面包含：** 使用 getpass 隐藏输入，检查三项字符串，通过临时文件写入并以 0600 权限原子替换根目录 .env.voice；只提示保存位置，不打印密钥。

**注意与关联：** 运行会创建或覆盖本机配置文件；停止并重新开始聆听后，新的识别器会读取配置。

**主要类/函数：** `main`。

#### `deploy/ningbo.service`

**用途：** 供 systemd 使用的后台服务模板。

**里面包含：** 指定 run.sh 作为启动命令，配置失败后重启、5 秒重启间隔、30 秒停止超时和 journal 日志。当前写死 User=dianxin、WorkingDirectory=/opt/test/ningbo。

**注意与关联：** 用户名和路径须按实际开发板核对。此文件在压缩包中存在不代表系统服务已经安装或启用；也不要与手动启动的同一项目同时抢占硬件。

#### `deploy/platform_check.py`

**用途：** 读取开发板系统信息，判断是否符合这个部署脚本的目标环境。

**里面包含：** inspect_platform() 读取设备树型号、nv_tegra_release、os-release 和 Python 版本；要求可识别的 Xavier NX、aarch64、L4T R35.3+、Ubuntu 20.04、Python 3.8，并返回报告及预设 PyTorch wheel 地址。

**注意与关联：** 这里描述的是脚本实际检查条件，不是本次从真实开发板读取到的系统版本，也不是对所有可能运行环境的兼容保证。

**主要类/函数：** `inspect_platform`。

#### `deploy/verify_models.py`

**用途：** 检查部署要求的模型文件是否与脚本中的固定 SHA256 一致。

**里面包含：** MODELS 列出两个人脸 ONNX 和一个 whisper-tiny.pt 的相对路径与哈希；verify() 流式读取文件计算摘要，缺失或不匹配时失败。

**注意与关联：** 只校验文件完整性，不执行模型前向；当前精简源码包不含这些模型，因此不能仅凭此脚本存在就视为校验已通过。

**主要类/函数：** `verify`。

## 五、想改某个功能，先找哪个文件

| 你要修改的内容 | 首先查看的位置 | 还需要同步核对 |
|---|---|---|
| 风扇、门锁、蜂鸣器引脚或高低电平 | 各自场景的 `jetson/config.py` | 对应 `gpio.py` / `lock_gpio.py` / `buzzer_gpio.py` 与实际接线。 |
| 舵机角度、脉宽、停留时间 | `experiments/servo/jetson/config.py` | `gpio.py` 的动作序列、`bridge.py` 的方向显示；不能把目标值当成实测值。 |
| 步进相序、速度、默认步数 | `experiments/stepper/jetson/config.py`、同场景 `bridge.py` | 输入地址在同场景 `serial_screen/addresses.py`；方向值目前在 `experiments/servo/serial_screen/config.py`。 |
| 某个屏幕按钮地址/返回值 | 该实验的 `serial_screen/addresses.py` | 屏幕编辑器里的真实配置；新增动作还要接入 `bridge.py`。 |
| 温度、湿度、距离小数位 | 该实验的 `serial_screen/screen.py` | 对应页面控件的小数位，避免代码缩放与页面解释不一致。 |
| 光照百分比 | `experiments/environment/jetson/readings.py` | `LightConfig.display_full_scale_lux`；当前为显示归一化。 |
| 烟雾串口或协议 | `experiments/smoke/jetson/config.py`、`smoke_protocol.py` | `driver.py`、`readings.py`；默认原始浓度不等于百分比。 |
| 串口屏波特率、自动找端口 | `hardware/screen/config.py`、`serial_discovery.py` | 烟雾串口应排除，不能让两者占用同一端口。 |
| 串口屏中文显示与文字容量 | `experiments/voice/jetson/config.py`、`experiments/voice/serial_screen/screen.py` | 页面编码/长度和缓冲区地址范围；不是只改字数变量。 |
| USB 摄像头、人脸阈值、录入样本数 | `experiments/access/jetson/config.py` | `engine.py`、`service.py` 及真实摄像头数据；当前不支持直接填 RTSP URL。 |
| 人脸验证后的开锁和蜂鸣 | `experiments/access/jetson/config.py`、`lock_control.py` | 成功通电 2 秒，失败蜂鸣 2 秒；仅手动验证触发。 |
| 麦克风、语音后端或分句 | `experiments/voice/jetson/config.py` | `microphone.py`、`recognizer.py` 与对应后端文件。 |
| 体温/测距的灯色或近距蜂鸣 | 对应实验 `jetson/feedback.py`；测距还有 `alarm.py` | 多场景共享灯的最终选择在 `hardware/rgb/feedback.py`。 |
| ADC 地址、通道、阈值 | `hardware/adc/config.py` | 电压反馈表与电流表分别核对；判断逻辑在 `actuator_checks.py`。 |
| 状态页某项显示地址或状态码 | `system/diagnostics/serial_screen/addresses.py` | 同目录 `screen.py`；没有 VP 的条目不会发送。 |
| 开机自启用户和目录 | `deploy/ningbo.service` | 实际用户名、安装路径，以及是否已有手动进程运行。 |

修改代码时，不要因文件名相同就到别的场景中修改。例如风扇的 `config.py` 与屏幕总连接的 `config.py` 是不同层次；先根据实验名称找到目录，再找文件。

## 六、包中没有、但运行可能需要的内容

以下是**配置引用或运行时生成的路径**，不是这次漏列的源码文件，也没有计入前面的 164 个文件。

| 路径 | 内容与产生方式 |
|---|---|
| `experiments/access/jetson/models/face_detection_yunet_2023mar.onnx` | 人脸检测权重，需另行提供；当前包内没有 `models/` 目录及该文件。 |
| `experiments/access/jetson/models/face_recognition_sface_2021dec.onnx` | 人脸特征权重，需另行提供。 |
| `experiments/voice/jetson/models/whisper-tiny.pt` | 本地 Whisper 权重，需另行提供；默认讯飞识别本身不调用它，但现有部署总脚本仍要求校验它。 |
| `.env.voice` | 真实讯飞配置，由模板复制填写或 `deploy/configure_voice.py` 创建；不是 `.env.voice.example`。 |
| `data/faces.sqlite3` | 人脸库，由人脸库初始化/识别服务创建，保存人员与特征。 |
| `data/engines/` | TensorRT 引擎缓存，按模型和运行环境构建时生成。 |
| `data/voice_usage.sqlite3` | 讯飞后端的本地调用次数记录，由 DailyQuota 创建。 |
| `.venv/` | 项目 Python 环境，由部署脚本创建或使用已有环境；不是源码的一部分。 |
| `logs/` | 部署日志和安装依赖快照，由 deploy.sh 的实际安装流程生成。 |
| `.deploy-cache/` | 部署下载缓存，例如 PyTorch wheel；由实际安装流程生成。 |

Vosk 使用的是另外指定的模型目录，代码会检查其中的 `am/final.mdl`。本包既不附带该模型，也没有在基础依赖清单中声明 `vosk`。切换后端不能只改一个字符串就假定所有资源已经就绪。

本包也不含串口屏设计软件原工程、背景图/字库工程、旧版备份、审查报告及 `tests/` 测试目录。`system/pin_test.py`、`system/diagnostics/` 和各识别模块的 `checks.py` 是仍存在的具体功能文件，不应与已删的测试资料混淆。

## 七、启动与检查入口

### 7.1 阅读代码时先做什么

把 `ningbo/` 作为 PyCharm 项目根目录。先看 `main.py`、`application.py`，再选一个实验阅读 `bridge.py`、`jetson/` 和 `serial_screen/`。最简单的实际调用链可从风扇实验开始，不必先读整个人脸 TensorRT 模块。

在已有依赖的解释器中，项目提供以下配置检查入口：

```bash
python main.py --check-config
```

该命令不启动 GPIO、摄像头、麦克风或串口采集；它检查的是配置结构，不能证明硬件接线、模型文件、密钥授权和所有冲突检查都正确。

### 7.2 需要明确区分的命令

| 命令 | 当前实现做什么 | 注意 |
|---|---|---|
| `python main.py --list-audio-devices` | 查询音频输入/输出设备。 | 不进入持续采音；需要音频枚举依赖。 |
| `python main.py --list-cameras` | 枚举视频节点并查询设备能力。 | 会查询节点，不进入持续视频识别；不是证明已有视频显示。 |
| `python main.py --list-faces` | 列出本项目数据库中的人员。 | 不采集图像；需有效数据库格式。 |
| `python main.py --init-face-db` | 创建/检查人脸数据库和模型指纹。 | 会读模型并写数据库，不能当作完全不改文件的检查。 |
| `python main.py --check-acceleration` | 执行人脸前向与所选语音后端检查。 | 需要模型/推理环境，可能构建引擎缓存；讯飞分支仅跳过本地 GPU 检查。 |
| `bash deploy.sh --plan` | 检查平台和模型后显示安装计划。 | 缺模型也会失败，不等于无条件展示说明。 |
| `bash deploy.sh --check-only` | 检查既有部署环境并执行前向检查。 | 需要已有 `.venv`、匹配平台及全部模型。 |
| `bash deploy.sh` | 实际安装环境、配置权限、编译采样器。 | 会联网并修改系统与项目环境；不是普通启动。 |
| `python main.py` / `bash run.sh` | 启动实际应用。 | 会连接硬件；run.sh 使用项目 `.venv`。 |

当前压缩包缺少模型等外部资源，不能直接宣称解压后九个场景都能完整运行。正常使用前仍需结合开发板确认环境、接线和串口屏工程；本说明没有替代这些检查。

## 八、版本与说明边界

本说明仅以 `NingBo_Minimal_NoOLED_20261016.zip` 为依据，通过目录清点、源码阅读、Python 语法树提取和二进制类型检查整理；覆盖 164 个文件、38 个子目录，另列出 ningbo/ 根目录。本次没有执行项目业务程序、安装脚本、模型推理或真实硬件控制，没有把此前任何测试结果当成本次结果。全部原始文件保持未修改；文档不代表历史 BUG 已修复或实机验收通过。

源压缩包 SHA256：

`cf4c2ce0c76ca4606d76ad65bd6acff34ce6d0bea290636e6b03cbe9505ea461`
