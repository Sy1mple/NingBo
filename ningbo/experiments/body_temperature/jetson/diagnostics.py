"""GY-906 状态检测：本轮有效的 SMBus 温度响应通过，短暂通信失败重试。"""
import time
from hardware.diagnostics import check_numeric_sensor


MAX_ATTEMPTS = 3
RETRY_SECONDS = 0.2


def check_body_temperature(manager):
    evidence = {"transport": "i2c_smbus", "check_scope": "temperature_response"}
    if "body_temperature" not in getattr(manager, "_readers", {}):
        return {**evidence, "status": "error", "attempts": 0,
                "note": "GY-906 驱动未启用，未收到温度响应"}
    if getattr(manager, "_pintest_mode", False):
        return {**evidence, "status": "error", "attempts": 0,
                "note": "引脚维护模式下无法进行新的温度采样"}
    cfg = manager.config.body_temperature
    started = time.time()
    result = {"status": "error", "note": "未收到有效温度数据"}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if getattr(manager, "_stop", None) is not None and manager._stop.is_set():
            return {**evidence, "status": "error", "attempts": attempt - 1,
                    "note": "设备服务已停止，未取得本轮有效响应"}
        try:
            # poll_sensor 直接读取芯片，不使用上一次状态检测或业务页面的缓存。
            # MLX90614 驱动验证两个寄存器的 PEC、错误位及物理量程。
            result = check_numeric_sensor(manager, "body_temperature",
                ("temperature", "ambient_temperature"),
                lambda state: cfg.temperature_range[0] <= state["temperature"] <= cfg.temperature_range[1]
                and cfg.ambient_range[0] <= state["ambient_temperature"] <= cfg.ambient_range[1])
            sample_time = result.get("sample_time")
            if result["status"] == "normal" and sample_time is not None and sample_time < started:
                result = {**result, "status": "error", "note": "温度数据早于本次检测，拒绝旧数据"}
        except Exception as exc:
            result = {"status": "error", "note": str(exc)}
        if result.get("status") == "normal":
            return {**result, **evidence, "attempts": attempt,
                    "note": "本轮读取到有效环境温度和目标温度；SMBus 数据校验通过"}
        if attempt < MAX_ATTEMPTS:
            stop = getattr(manager, "_stop", None)
            if stop is None:
                time.sleep(RETRY_SECONDS)
            elif stop.wait(RETRY_SECONDS):
                break
    return {**result, **evidence, "status": "error", "attempts": attempt,
            "note": "未读取到本轮有效温度响应：" + result.get("note", "通信或数据校验失败")}
