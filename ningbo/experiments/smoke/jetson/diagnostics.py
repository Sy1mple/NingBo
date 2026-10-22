"""烟雾状态检测：本轮无完整有效数据就报异常，不等待重连或沿用旧帧。"""
import math


def _result_from_state(state):
    if state.get("status") in ("error", "disabled", "unconfigured"):
        return state
    if state.get("protocol") == "ffaa":
        value = state.get("concentration")
        valid = (state.get("status") == "normal"
                 and state.get("fresh_frame_received", True) is True
                 and not isinstance(value, bool) and isinstance(value, int)
                 and 0 <= value <= 0xFFFF)
        return {**state, "status": "normal" if valid else "error",
                "note": ("本轮完整 FFAA 烟雾帧接收正常；单位未标定" if valid
                         else "本轮未收到完整有效烟雾帧，请检查传感器连接与供电")}
    if not state.get("online"):
        return {**state, "status": "error", "note": "烟雾串口本次未收到有效数据"}
    if state.get("percent") is None:
        return {**state, "status": "error" if state.get("decoder_configured") else "unconfigured",
                "note": "串口收到字节，但本轮未取得经过协议校验的测量值"}
    percent = state["percent"]
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 0 <= percent <= 100:
        return {**state, "status": "error", "note": "烟雾测量值无效或超出范围"}
    return {**state, "status": "normal", "note": "烟雾协议解码器已提供有效测量值"}


def check_smoke(manager):
    acquire = getattr(manager, "acquire_sensor", None)
    release = getattr(manager, "release_sensor", None)
    managed_session = callable(acquire) and callable(release)
    if not managed_session:
        return _result_from_state(manager.poll_sensor("smoke"))
    acquire("smoke", "smoke-diagnostics")
    try:
        # 驱动读操作有独立串口超时；状态检测不额外等待或重建 UART。
        return _result_from_state(manager.poll_sensor("smoke"))
    finally:
        release("smoke", "smoke-diagnostics")
