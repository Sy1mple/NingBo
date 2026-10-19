"""Fan screen buttons directly control the physical fan; no invented feedback."""


class FanScreenBridge:
    def __init__(self, devices, config, screen=None):
        self.devices = devices
        self.config = config
        self.screen = screen

    def handle_event(self, event):
        name = event["event"] if isinstance(event, dict) else event
        if name in ("fan.start", "fan.stop"):
            result = self.devices.control("fan", on=(name == "fan.start"))
            return {"event": name, "handled": True, "sent": [], "result": result}
        return None
