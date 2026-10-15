# 宁波工程开发板部署

## 一键安装

将 `NingBo_Linux_20261013.tar.gz` 复制到 Jetson Xavier NX，在普通用户有写入权限的目录解压。不要用 sudo 解压到 root 所有的目录：

```bash
tar -xzf NingBo_Linux_20261013.tar.gz
cd NingBo_Linux
```

进入 `NingBo_Linux` 目录后执行：

```bash
bash deploy.sh
```

脚本会需要联网和 sudo 密码。面向 **JetPack 5.1.1 及之后的 5.1.x、L4T R35.3+、Ubuntu 20.04、系统 Python 3.8**。脚本先核对实际型号、架构、系统版本和三个模型的 SHA256；不匹配时在安装前停止并指出原因。JetPack 4、其他发行版或改过默认 Python 的系统不能按此脚本自动安装，需先使用匹配微雪载板的 JetPack 系统。脚本不负责刷机。

安装内容包含 Ubuntu 音频和视频库、I2C 工具、JetPack CUDA/cuDNN/TensorRT 软件组件、项目 `.venv`、NumPy/OpenCV/SciPy、Jetson.GPIO、串口依赖、CUDA PyTorch、Whisper 和分词依赖。默认 CUDA PyTorch 地址来自 [NVIDIA 官方安装指南](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html)，兼容关系参考 [Jetson PyTorch 版本表](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform-release-notes/pytorch-jetson-rel.html)。若现有 CUDA PyTorch 已通过真实 GPU 运算就复用，否则在项目虚拟环境安装官方 cp38 ARM64 wheel，不卸载系统版本。

系统必须已有 JetPack 配置的 NVIDIA apt 源和正常 GPU 驱动。apt 软件组件缺失可安装，底层镜像/内核不匹配不能靠 Python 依赖修复。系统库从 apt 下载、Python 库从 pip 下载，模型文件已随包提供；这是联网安装包，完整离线环境 wheel 未打包。

## 安装完成后启动

部署脚本配置 GPIO、I2C、串口、视频和音频的用户组权限。完成后重新登录终端一次，再按实际设备路径启动：

```bash
bash run.sh --port auto --camera-device auto --voice-device auto
```

默认 auto 会重新扫描串口屏、USB 摄像头和 USB 麦克风，无需填写变化的设备编号；需要固定某个设备时仍可用参数覆盖。所有参数继续由 `JETSON/Sensor/Config.py` 管理，地址由 `Serial_Screen/screen_service/screen/addresses.py` 管理。启动脚本把其他参数原样传给 `main.py`。

```bash
bash run.sh --list-audio-devices
bash run.sh --list-cameras
bash run.sh --list-faces
bash run.sh --rename-face 1 "张三"
```

GPIO 使用 BOARD 排针物理编号，完整接线见 `docs/宁波实验箱接线说明.docx`。UART、I2C 和 GPIO 的 pinmux 需按实际微雪载板确认；脚本不会猜测载板配置并修改设备树。没有安装的 OLED 应在 Config.py 关闭；人体温度已接入 GY-906/MLX90614，与光照传感器共用 I2C8，详见 `GY906_INTEGRATION.md`；烟雾已接入 FFAA 串口协议，物理 8/10 脚对应 ttyTHS0，详见 `SMOKE_UART_INTEGRATION.md`。环境安装完成不代表这些未确认硬件已自动接入。

## 检查和日志

```bash
bash deploy.sh --plan
bash deploy.sh --check-only
bash run.sh --check-config
```

`--plan` 只核对平台、模型并显示安装计划。`--check-only` 使用已安装虚拟环境运行配置和 GPU 模型前向。正式安装最后也会运行人脸检测、特征提取、Whisper 前向及单元测试，全部通过后才显示部署成功；不会在安装验证中打开外设或启动执行器。首次 TensorRT 编译需要时间，结果保存在 `data/engines/`。

日志位于 `logs/deploy_日期_时间.log`，安装后的依赖记录为 `logs/requirements_installed.txt`。失败可修复网络或具体错误后重复执行；现有 `.venv` 会复用，人员库不会清空。不要把其他电脑/板子的 TensorRT engine 直接当作可移植模型。

若 NVIDIA 下载受网络限制，可以先下载官方 wheel 再传入：

```bash
bash deploy.sh --torch-wheel /path/to/torch-2.0.0+nv23.05-cp38-cp38-linux_aarch64.whl
```

GPU 不可用时明确失败，默认不回到 CPU。DLA 用法和细项配置见根目录 `README.md`。

## 断电重启后的自动启动

当前板已安装并启用 ningbo.service。运行用户 dianxin，项目目录 /opt/test/NingBo_Linux，屏幕启动时自动扫描 USB 串口并验证屏幕应答。

同型号 CH340 的通用 by-id 别名可能重名。服务现已取消固定 USB 编号和物理插孔，按两次只读 VP 应答识别串口屏；未找到时服务会报错并按 systemd 策略重试。

手动 run.sh 或 nohup 不会自动在重启后运行。服务启动后等待串口屏按钮，传感器后台采集，执行器由按钮控制；程序异常退出后 5 秒重试。

```bash
sudo install -m 0644 deploy/ningbo.service /etc/systemd/system/ningbo.service
sudo systemctl daemon-reload
sudo systemctl enable --now ningbo.service
systemctl status ningbo.service
journalctl -u ningbo.service -b -n 50 --no-pager
sudo systemctl restart ningbo.service
```

其他板安装前应修改服务模板中的用户名、目录和屏幕路径，并先停止手动启动的项目进程。启用服务后不要另外启动 run.sh，以免重复占用串口/GPIO。停止用 sudo systemctl stop ningbo.service；取消开机启动用 sudo systemctl disable --now ningbo.service。

服务日志使用 journalctl 查看；logs/runtime_20261013.log 是此前手动运行的历史日志。
