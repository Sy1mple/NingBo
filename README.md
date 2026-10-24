# 宁波智能实验箱

当前正式项目位于 [ningbo](ningbo/README.md)，与开发板 `/opt/ningbo` 同步。

2026-10-24 步进方向文字修复需要配套下载 [串口屏工程](hmi/README.md)。程序改用独立文本地址 `0x1300`，仅更新开发板无法更新屏幕内部工程。

请从 `ningbo/` 目录部署和运行，安装及双服务说明见 [部署文档](ningbo/deploy/SCREEN_SERVICES.md)。

代码按 `experiments/` 场景、`hardware/` 共用驱动、`system/diagnostics/` 状态检测拆分；测试位于 `ningbo/tests/`。

真实密钥、虚拟环境、运行日志和人脸数据库不包含在仓库中；配置格式参考 `.env.face.example` 与 `.env.voice.example`。

历史旧版目录已移除，可通过 Git 历史查阅。`docs/` 保留接线说明与历史校准资料，使用时以当前代码配置为准。
