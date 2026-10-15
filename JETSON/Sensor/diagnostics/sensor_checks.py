"""传感器及状态采样模块的有效性检查。"""
import math


class SensorStatusChecker:
    def __init__(self, manager):
        self.manager = manager

    def check(self, name):
        if name == "body_temperature" and name not in getattr(self.manager, "_readers", {}):
            return {"status": "unconfigured", "note": "人体温度型号及驱动尚未提供"}
        if name == "current":
            return self._check_current_monitor()
        return self._check_reader(name)

    def _check_reader(self, name):
        state = self.manager.poll_sensor(name)
        if state.get("status") in ("error", "disabled"):
            return state
        if name == "smoke":
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
        fields = {"dht11": ("temp", "humidity"), "bh1750": ("lux",), "ultrasonic": ("distance",),
                  "body_temperature": ("temperature",)}[name]
        if not all(isinstance(state.get(field), (int, float)) and not isinstance(state.get(field), bool)
                   and math.isfinite(state[field]) for field in fields):
            return {**state, "status": "error", "note": "测量值缺失或无效"}
        config = self.manager.config
        valid = True
        if name == "dht11":
            valid = (config.dht11.temperature_range[0] <= state["temp"] <= config.dht11.temperature_range[1]
                     and config.dht11.humidity_range[0] <= state["humidity"] <= config.dht11.humidity_range[1])
        elif name == "bh1750":
            valid = state["lux"] >= 0
        elif name == "ultrasonic":
            valid = 0 <= state["distance"] <= config.ultrasonic.maximum_distance_cm
        elif name == "body_temperature":
            # 驱动以摄氏度返回有效数值，业务高温判定在独立的 RGB 规则中。
            low, high = config.body_temperature.temperature_range
            valid = low <= state["temperature"] <= high
        if not valid:
            return {**state, "status": "error", "note": "测量值超出配置范围"}
        return {**state, "status": "normal", "note": "采集及驱动校验通过"}

    def _check_current_monitor(self):
        config = self.manager.config.current
        if not config.enabled:
            monitor = getattr(self.manager, "adc_monitor", None)
            if monitor is not None and self.manager.config.ads1115.enabled:
                voltage = monitor.read_all()
                return {**voltage, "status": "unconfigured", "adc_status": "normal",
                        "note": "ADS1115 四路电压已读取；设备通道和电流换算尚未确认"}
            return {"status": "unconfigured", "note": "ADS1115 电流检测尚未启用"}
        channels = {}
        for name, channel in config.channels.items():
            channels.setdefault(channel.address, name)
        if not channels:
            return {"status": "unconfigured", "note": "未配置 ADS1115 通道"}
        values = {name: self.manager.current_monitor.read_ma(name) for name in channels.values()}
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("电流测量结果不是有限数值")
        return {"status": "normal", "currents_ma": values, "note": "已配置 ADC 的读取和电流换算通过"}
