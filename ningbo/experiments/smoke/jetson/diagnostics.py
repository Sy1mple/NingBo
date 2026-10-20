"""烟雾场景：串口在线、协议解码及浓度有效性检查。"""
import math
import time


def _result_from_state(state):
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


def check_smoke(manager):
    acquire = getattr(manager, "acquire_sensor", None)
    release = getattr(manager, "release_sensor", None)
    managed_session = callable(acquire) and callable(release)
    if not managed_session:
        return _result_from_state(manager.poll_sensor("smoke"))

    acquire("smoke", "smoke-diagnostics")
    try:
        deadline = time.monotonic() + manager.config.smoke.diagnostic_timeout_seconds
        reconnect = getattr(manager, "reconnect_sensor", None)
        reconnect_interval = manager.config.smoke.diagnostic_reconnect_interval_seconds
        next_reconnect = time.monotonic() + reconnect_interval
        reconnect_count = 0
        last_state = None
        while True:
            state = manager.poll_sensor("smoke")
            last_state = state
            result = _result_from_state(state)

            # 完整且通过校验的测量帧，或明确的配置/打开/读取故障，才是确定结果。
            if result.get("status") == "normal" or result.get("status") in ("disabled", "unconfigured"):
                return result
            note = str(result.get("note", ""))
            transient_no_frame = (
                state.get("protocol") == "ffaa"
                and (state.get("status") == "unknown" or "有效帧超时" in note)
            )
            if result.get("status") == "error" and not transient_no_frame:
                return result

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    **(last_state or {}), "status": "error",
                    "note": (f"{manager.config.smoke.diagnostic_timeout_seconds:g} 秒内未收到完整 FF AA H L 3C 烟雾帧；"
                             f"已自动重连 {reconnect_count} 次"),
                }
            now = time.monotonic()
            if callable(reconnect) and now >= next_reconnect:
                reconnect("smoke", "smoke-diagnostics")
                reconnect_count += 1
                next_reconnect = now + reconnect_interval
            time.sleep(min(0.1, remaining))
    finally:
        # result 已经在上面形成后，Python 才执行 finally：因此每次都是
        # “明确正常/异常结果 -> 释放串口 -> 把结果交给状态检测服务”。
        release("smoke", "smoke-diagnostics")
