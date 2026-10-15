# 讯飞免费额度语音识别

## 开通及填写密钥

1. 在 https://www.xfyun.cn 注册并创建 WebAPI 应用。
2. 给应用开通 **语音听写（流式版）** 的默认免费额度。不要选择语音识别大模型、实时语音转写或购买付费套餐。
3. 在该服务页面取得 AppID、APIKey、APISecret。实际免费额度以你的控制台为准；官方文档当前说明创建应用默认每日 500 次。
4. 开发板项目根目录 `/opt/test/NingBo_Linux/.env.voice` 已准备好。只填写等号右边的值：

```ini
XFYUN_APP_ID=你的AppID
XFYUN_API_KEY=你的APIKey
XFYUN_API_SECRET=你的APISecret
```

密钥不写入 Config.py，不打印到日志。配置文件须仅允许运行用户读取（权限 600），不要放进部署压缩包或聊天截图。也可通过同名环境变量配置；环境变量优先。

推荐在开发板终端使用隐藏输入脚本填写（不需要将密钥发给助手）：

```bash
cd /opt/test/NingBo_Linux
.venv/bin/python deploy/configure_voice.py
```

依次输入三项密钥，输入时不会回显。也可按上面的格式直接编辑配置文件。填写后点击停止聆听，再点击开始聆听，程序会重新读取密钥。如需重启整个服务：

```bash
sudo systemctl restart ningbo.service
journalctl -u ningbo.service -b -n 30 --no-pager
```

然后在串口屏点击“开始聆听”，靠近麦克风说话，观察中间结果和最终结果；停止按钮会关闭输入及连接并清空文字。

## 工作方式

默认 `voice.backend="xfyun"`。AIMIC-M4 以 48 kHz 采音，程序连续滤波转成 16 kHz 单声道 int16 PCM，以约 40 ms 的帧发送。未说话时只保留短音频缓冲，不建立云端会话；检测到说话后才连接。使用动态修正和简体中文，识别中间结果以 `partial_interval` 限速写入原地址 `0x1064`。屏幕仍使用已经下载的 GB2312 字库。

句末静音触发结束帧，等待最终结果，然后关闭本次连接并等待下一句话。每个会话在 50 秒音频以内结束，避免平台 60 秒限制。云端后端不加载本地 Whisper 模型，需要开发板能联网；模型推理在讯飞服务端进行。

## 免费调用上限

`voice.xfyun_daily_limit=450` 是本项目上限，最大只能配置到 450。每次连接前记录一次，失败连接也保守计数，停止按钮不会退款或减计数。记录保存到 `data/voice_usage.sqlite3`，按北京时间日期和 AppID 分组，程序重启及断电不会清空同日记录。达到上限后拒绝建立连接，不切换其他接口，也不自动购买套餐。

这是本项目的本地计数，并不能读取讯飞账户总用量；同 AppID 被其他程序使用也会消耗平台额度。建议为本实验箱单独创建应用，并保留默认免费服务。不要人为清空计数数据库来绕过上限。

网络、授权或额度错误会结束当前语音任务并记录错误码。处理后重新点击开始。密钥未填时其他传感器和执行器正常运行，语音明确报“未配置讯飞密钥”。没有密钥时完成的测试属于模拟网络协议测试，不能替代真实识别准确率和延迟测试。

参数统一在 `JETSON/Sensor/Config.py` 的 `VoiceConfig` 中。需要回到原本地模型时显式传入 `--voice-backend whisper_cuda`，不会自动回退。

官方文档：https://www.xfyun.cn/doc/asr/voicedictation/API.html

## 正式程序依赖环境

run.sh 禁用用户目录依赖。使用 PYTHONNOUSERSITE=1 .venv/bin/python -m pip install websocket-client==1.8.0 安装到项目虚拟环境；接口测试也使用相同隔离设置。实机已安装并通过相同环境的授权和会话测试。
