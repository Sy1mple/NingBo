"""宁波工程正式入口：Jetson 设备服务与 JGUS 串口屏。"""
import argparse
from dataclasses import replace
import logging
import signal

from application import NingBoApplication
from device_service import DEFAULT_CONFIG

LOGGER = logging.getLogger("ningbo")


def main(argv=None):
    parser = argparse.ArgumentParser(description="宁波开发板、传感器、执行器与串口屏")
    parser.add_argument("--port", help="串口屏：默认 auto 按协议扫描，也可手动指定设备路径")
    parser.add_argument("--baudrate", type=int, help="覆盖串口屏波特率")
    parser.add_argument("--check-config", action="store_true", help="检查并打印配置，不打开硬件")
    parser.add_argument("--check-acceleration", action="store_true", help="执行人脸/语音模型真实前向，检查 CUDA，不打开外设")
    parser.add_argument("--cuda-device", type=int, help="覆盖人脸和语音使用的 CUDA GPU 编号")
    parser.add_argument("--face-backend", choices=("xfyun", "tensorrt", "opencv_cpu"), help="人脸后端；默认讯飞云端 API")
    parser.add_argument("--face-dla-core", type=int, choices=(0, 1), help="TensorRT 尝试使用 Xavier NX DLA 核 0/1")
    parser.add_argument("--face-dla-strict", action="store_true", help="禁止 DLA 不支持的层转 GPU，构建不兼容时直接报错")
    parser.add_argument("--voice-backend", choices=("xfyun", "whisper_cuda", "vosk"), help="语音后端；默认讯飞免费额度接口")
    parser.add_argument("--list-audio-devices", action="store_true", help="列出音频输入/输出设备，不打开麦克风或 GPIO")
    parser.add_argument("--voice-device", "--voice-input-device", dest="voice_device",
                        help="麦克风默认 auto 扫描 USB；也可指定编号、名称、hw:声卡号,设备号或采音节点")
    parser.add_argument("--voice-model", help="Whisper .pt 文件；显式选择 Vosk 时为旧模型目录")
    parser.add_argument("--camera-device", help="USB 摄像头：默认 auto 扫描图像节点，也可指定编号或设备路径")
    parser.add_argument('--preview-port', type=int, help='摄像头预览端口，默认 8765')
    parser.add_argument('--no-camera-preview', action='store_true', help='关闭网页预览，仅执行人脸任务')
    parser.add_argument('--no-local-browser', action='store_true', help='不自动打开开发板本地火狐，保留电脑端预览')
    parser.add_argument("--face-db", help="覆盖 SQLite 人脸库路径")
    parser.add_argument("--enroll-name", help="下一位成功新录入的姓名；省略时自动编号")
    face_actions = parser.add_mutually_exclusive_group()
    face_actions.add_argument("--list-cameras", action="store_true", help="只列出 Linux 摄像头节点，不打开设备")
    face_actions.add_argument("--list-faces", action="store_true", help="只列出已录入人员，不打开硬件")
    face_actions.add_argument("--init-face-db", action="store_true", help="创建或检查人脸库，不打开硬件")
    face_actions.add_argument("--rename-face", nargs=2, metavar=("ID", "NAME"), help="在终端修改库内人员姓名，不打开硬件")
    args = parser.parse_args(argv)
    screen = DEFAULT_CONFIG.screen
    if args.port is not None:
        screen = replace(screen, port=args.port)
    if args.baudrate is not None:
        screen = replace(screen, baudrate=args.baudrate)
    voice = DEFAULT_CONFIG.voice
    if args.voice_device is not None:
        device = args.voice_device.strip()
        voice = replace(voice, input_device=int(device) if device.isdecimal() else device)
    if args.voice_model is not None:
        voice = replace(voice, model_path=args.voice_model)
    camera, face = DEFAULT_CONFIG.camera, DEFAULT_CONFIG.face
    if args.preview_port is not None:
        camera = replace(camera, preview_port=args.preview_port)
    if args.no_camera_preview:
        camera = replace(camera, preview_enabled=False)
    if args.no_local_browser:
        camera = replace(camera, preview_local_browser=False)
    if args.cuda_device is not None:
        face = replace(face, cuda_device=args.cuda_device)
        voice = replace(voice, cuda_device=args.cuda_device)
    if args.face_backend is not None:
        face = replace(face, backend=args.face_backend)
        if args.face_backend != DEFAULT_CONFIG.face.backend and args.face_db is None:
            from pathlib import Path
            filename = 'faces_xfyun.sqlite3' if args.face_backend == 'xfyun' else 'faces.sqlite3'
            face = replace(face, database_path=str(Path(face.database_path).with_name(filename)))
    if args.face_dla_core is not None:
        face = replace(face, dla_core=args.face_dla_core)
    if args.face_dla_strict:
        if args.face_dla_core is None and face.dla_core is None:
            parser.error("--face-dla-strict 需要指定 --face-dla-core")
        face = replace(face, dla_gpu_fallback=False)
    if args.voice_backend is not None:
        voice = replace(voice, backend=args.voice_backend)
    if args.camera_device is not None:
        device = args.camera_device.strip()
        camera = replace(camera, device=int(device) if device.isdecimal() else device)
    if args.face_db is not None:
        face = replace(face, database_path=args.face_db)
    if args.enroll_name is not None:
        face = replace(face, enroll_name=args.enroll_name)
    config = replace(DEFAULT_CONFIG, screen=screen, voice=voice, camera=camera, face=face)
    try:
        config.validate()
    except ValueError as exc:
        parser.error(str(exc))
    if args.check_acceleration:
        import json
        from experiments.access.jetson.checks import check_acceleration as check_access
        from experiments.voice.jetson.checks import check_acceleration as check_voice
        reports = [check_access(config), check_voice(config)]
        report = {"ok": all(item["ok"] for item in reports), "results": reports}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if args.list_cameras:
        from experiments.access.jetson.camera import camera_nodes
        nodes = camera_nodes()
        for node in nodes:
            print(f'{node["path"]} -> {node["target"]}; USB={node.get("usb", False)}, 图像采集={node.get("capture", False)}; {node.get("name", node.get("error", ""))}')
        if not nodes:
            print("未找到 Linux 摄像头节点；接入后在 Jetson 执行此命令。Windows 可传 --camera-device 0。")
        return 0
    if args.list_faces or args.init_face_db or args.rename_face:
        from experiments.access.jetson.library import FaceLibrary
        from experiments.access.jetson.features import model_signature
        library = FaceLibrary(config.face.database_path)
        if config.face.backend == 'xfyun':
            from experiments.access.jetson.cloud_library import CloudFaceLibrary
            library = CloudFaceLibrary(config.face.database_path)
        try:
            if args.init_face_db:
                if config.face.backend == 'xfyun':
                    library.initialize()
                else:
                    library.initialize(model_signature(config.face), config.face.feature_dimension)
                print(f"人脸库已就绪：{library.path}")
            elif args.rename_face:
                person_id, name = args.rename_face
                if not person_id.isdecimal() or int(person_id) <= 0:
                    raise ValueError("人员编号须为正整数，请先用 --list-faces 查看")
                library.rename(int(person_id), name)
                print(f"人员 {person_id} 已改名为：{name.strip()}；正在运行的识别需停止再启动以刷新人脸库。")
            else:
                people = library.list_people()
                for person in people:
                    print(f'{person["id"]}: {person["name"]}; 样本={person["samples"]}; 录入时间={person["created_at"]}')
                if not people:
                    print("人脸库为空，请接入摄像头后点击串口屏上的人脸录入按钮。")
        except Exception as exc:
            parser.error(f"人脸库操作失败: {exc}")
        return 0
    if args.list_audio_devices:
        from experiments.voice.jetson.microphone import audio_inputs, audio_outputs
        try:
            devices = {device['index']: device for device in audio_inputs() + audio_outputs()}
            for device in devices.values():
                print(f'{device["index"]}: {device["name"]}; 输入通道={device["max_input_channels"]}, '
                      f'输出通道={device.get("max_output_channels", 0)}, 默认采样率={device["default_samplerate"]}')
        except Exception as exc:
            parser.error(f"音频设备查询失败: {exc}")
        return 0
    if args.check_config:
        print(f"串口屏: {config.screen.port}, {config.screen.baudrate}, 8N1")
        print(f"人体温度: enabled={config.body_temperature.enabled}; 型号={config.body_temperature.model}; "
              f"I2C{config.i2c_bus}/0x{config.body_temperature.address:02X}")
        print(f"烟雾: {config.smoke.port}, {config.smoke.baudrate}, {config.smoke.protocol}; enabled={config.smoke.enabled}; BOARD 8=TX/10=RX")
        print(f"GPIO 编号: {config.gpio_numbering}; I2C 总线: {config.i2c_bus}")
        print(f"GPIO 设备: {config.gpio_devices()}")
        voice_target = config.voice.xfyun_endpoint if config.voice.backend == "xfyun" else config.voice.model_path
        print(f"USB 语音: enabled={config.voice.enabled}; 设备={config.voice.input_device}; 后端={config.voice.backend}; 目标={voice_target}")
        print(f"语音文本: 编码={config.voice.text_encoding}; 缓冲区={config.voice.text_words * 2} Byte")
        print(f"识别后端: 人脸={config.face.backend}; 语音={config.voice.backend}")
        print(f"USB 摄像头: 设备={config.camera.device}; {config.camera.width}x{config.camera.height}, 请求 FPS={config.camera.fps}")
        print(f'摄像头预览: enabled={config.camera.preview_enabled}; http://127.0.0.1:{config.camera.preview_port}/; '
              f'本地火狐={config.camera.preview_local_browser}；相机由屏幕按钮启动')
        print(f"人脸: enabled={config.face.enabled}; 人脸库={config.face.database_path}; 自动开锁={config.face.unlock_on_match}")
        if config.face.backend == 'xfyun':
            print(f"人脸 API: 讯飞 HTTPS；配置={config.face.credentials_file}；本机用量={config.face.usage_file}")
            print("人脸使用本机照片库，无需 ONNX/TensorRT；免费次数和期限须按控制台填写。")
        else:
            print(f"检测模型={config.face.detector_path}; 特征模型={config.face.recognizer_path}")
        print("配置结构校验通过；实际接线和设备路径需在开发板确认。")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    previous_handlers = {}
    try:
        app = NingBoApplication(config)

        def request_shutdown(signum, frame):
            LOGGER.info("收到停止信号 %s，开始关闭设备服务", signum)
            app.request_stop()

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_shutdown)
        with app:
            app.run()
    except KeyboardInterrupt:
        LOGGER.info("收到 Ctrl+C，设备服务已关闭")
    except Exception:
        LOGGER.exception("工程运行失败")
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
