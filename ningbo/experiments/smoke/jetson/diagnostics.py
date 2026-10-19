"""烟雾场景：串口在线、协议解码及浓度有效性检查。"""
import math


def check_smoke(manager):
    state = manager.poll_sensor("smoke")
    if state.get("status") in ("error", "disabled"):
        return state
    if state.get("protocol") == "ffaa":
        if state.get("status") != "normal":
            return state
        value = state.get("concentration")
        valid = not isinstance(value, bool) and isinstance(value, int) and 0 <= value <= 0xFFFF
        return {**state, "status": "normal" if valid else "error",
                "note": "FFAA 原始浓度帧接收正常；单位未标定" if valid else "烟雾浓度数值无效"}
    if not state.get("online"):
        return {**state, "status": "error", "note": "烟雾串口本次未收到有效数据"}
    if state.get("percent") is None:
        status = "unknown" if state.get("decoder_configured") else "unconfigured"
        return {**state, "status": status, "note": "串口收到字节，尚未取得经过协议校验的测量值"}
    percent = state["percent"]
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 0 <= percent <= 100:
        return {**state, "status": "error", "note": "烟雾测量值无效或超出范围"}
    return {**state, "status": "normal", "note": "烟雾协议解码器已提供有效测量值"}
