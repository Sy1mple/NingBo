# 项目与调试资料索引

> **当前运行版本（2026-10-19）**：[ningbo/](ningbo/) 是从 `192.168.1.106:/opt/ningbo` 同步的场景模块化工程，本地对应 `NingBo_Minimal_NoOLED_20261016/ningbo`。入口为 `ningbo/main.py`，配置为 `ningbo/config.py`；下文根目录工程保留为旧版参考。同步与验证记录见 [docs/SOURCE_SYNC_20261019.md](docs/SOURCE_SYNC_20261019.md)。

本地正式项目：`C:\Users\ZZH\PycharmProjects\NingBo`。2026-10-15 已从 `dianxin@192.168.1.121:/opt/test/NingBo_Linux` 同步运行代码和模型。


## 从哪里开始

| 用途 | 位置 |
| --- | --- |
| 启动程序、设备配置、接线和屏幕协议 | [README.md](README.md) |
| Linux 部署、依赖及服务安装 | [deploy/README_LINUX.md](deploy/README_LINUX.md) |
| 硬件测试、采样和调参入口 | [tools/README.md](tools/README.md) |
| 当前传感器与执行器配置 | `JETSON/Sensor/Config.py` |
| 串口屏地址、按键值 | `Serial_Screen/screen_service/screen/addresses.py` |
| 自动回归测试 | `tests/`，在项目根目录运行 `python -m unittest discover -s tests -v` |
| ADC、体温、烟雾集成说明 | `ADS1115_INTEGRATION.md`、`GY906_INTEGRATION.md`、`SMOKE_UART_INTEGRATION.md` |

## 整理后的目录

- `JETSON/`、`Serial_Screen/`、`business/`：远端当前运行的业务代码。
- `deploy/`：保留原路径的部署脚本、只读采样工具和执行器测试工具，避免已有命令失效。
- `tools/standalone/oled/`：从远端用户目录收回的四个 OLED 独立实验脚本。
- `tools/notebooks/`：原始步进电机示例及两个未命名实验 Notebook，保留原文件名便于追溯。
- `archive/jetson_20261015/`：旧版补丁、配置副本、历史备份包及旧测试代码。它们不是当前配置，也不应在当前项目重放。
- `artifacts/jetson_20261015/`：ADC、RGB、舵机、蜂鸣器、烟雾、USB 音视频测试结果，以及远端日志、数据库一致性快照和服务配置快照。
- `docs/sync_20261015.json`：同步前差异、源文件到本地文件的对应关系及 SHA256。
- `docs/remote_export_20261015.json`：远端导出的原始清单。

## 本次同步边界

远端业务代码作为当前基准。本地原有的人脸数据库 `data/faces.sqlite3` 和 IDE 设置保留；远端数据库另存于 `artifacts/jetson_20261015/runtime/data/`。语音密钥 `.env.voice`、虚拟环境、下载缓存、生成的 TensorRT 引擎和 shell 历史不混入同步源码。

业务逻辑保持远端版本，本地另行更新目录索引、忽略规则、过期的 README 步进说明和两份测试文件。原项目及远端导出包保存在 `C:\Users\ZZH\Documents\ChatGPT\宁波开发\sync_20261015\`，可完整追溯本次覆盖内容。

本次整理没有重启远端服务，也没有自动执行电机、舵机或补丁脚本。若要将本地后续修改部署回设备，应单独执行部署流程。

## 校验结果

201 项自动测试通过，92 个 Python 文件通过 Python 3.8 语法检查，三个必需模型通过 SHA256 校验，配置检查通过。原本地人脸库哈希保持不变；两份远端数据库快照通过完整性检查。最终复核远端 132 个项目文件与导出时一致，ningbo 服务仍在运行。

## 最新复核（2026-10-15 11:29）

再次从 `192.168.1.121` 拉取清单并逐文件校验。132 个远端项目文件及此前收集的 home 测试资料均未发生内容变化；仅运行服务状态快照更新，服务当前正常运行。本地目录索引、文档及测试整理保留。详情见 [复核记录](docs/sync_refresh_20261015_1129.json)。

## GitHub 上传前同步（2026-10-15 晚间）

已从开发板重新导出并逐文件校验 137 个源码及资料文件，补齐 4 个独立 RGB 测试脚本；远端夹在业务目录中的旧代码副本收进本地历史归档。保留本地目录索引、忽略规则及回滚说明，详见 [本轮同步清单](docs/source_sync_20261015_github.json)。

当前 RGB 物理引脚为红 12、绿 35、蓝 38。门禁灯色修改已回滚；阈值保持本轮标定前版本。串口屏、USB 摄像头及 AIMIC-M4 使用自动选择设备的逻辑。MQ2 间歇停发的现场保留步骤见 [烟雾 UART 说明](SMOKE_UART_INTEGRATION.md)。

GitHub 源码不包含语音密钥、数据库、下载模型、生成引擎、历史归档和原始采样文件；这些资料保留在本地。部署前按 README 下载或复制模型，并在设备上单独配置凭据。

## 阈值标定回退（2026-10-15）

已按用户要求恢复本轮标定前代码。当前仅保留原有舵机反馈阈值 10.000/11.500 mV；本轮新增的风扇、蜂鸣器、步进阈值已撤回。原有状态检测、串口屏控制和 ADC 通道映射保留。采样记录及报告作为历史资料保留，不能视为当前已应用配置。详情见 `docs/threshold_rollback_20261015.json`。
