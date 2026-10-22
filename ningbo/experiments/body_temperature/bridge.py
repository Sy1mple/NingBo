"""body_temperature: complete local screen-to-hardware bridge, without a base bridge."""
from .jetson.readings import SnapshotReader


class BodyTemperatureBridge:
    scene = 'body_temperature'
    clear_values = {'temperature': 0}

    def __init__(self, context):
        self.context = context
        self.screen = context.screen
        self.devices = context.devices
        self.reader = SnapshotReader(context.config, context.feedback.wall_clock, context.light_full_scale_lux)

    def handle_event(self, event):
        name = event["event"] if isinstance(event, dict) else event
        if name not in (self.scene + ".start", self.scene + ".stop"):
            return None
        context = self.context
        if name.endswith(".start"):
            context.active_scenes[self.scene] = context.clock()
            context.feedback.activate(self.scene)
            self.on_start()
            sent = context.tick()
            return {"event": name, "handled": True, "sent": sent,
                    "note": self.start_note(self.devices.snapshot())}
        context.active_scenes.pop(self.scene, None)
        context.feedback.release(self.scene)
        self.on_stop()
        context._flush_feedback()
        sent = self.screen.handle_message({"scene": self.scene, **self.clear_values})
        return {"event": name, "handled": True, "sent": sent, "note": "采集已停止"}

    def on_start(self):
        self.context.body_temperature_alarm.activate()

    def on_stop(self):
        self.context.body_temperature_alarm.release()
        self.context._flush_body_temperature_alarm()

    def start_note(self, snapshot):
        state = snapshot.get("sensors", {}).get("body_temperature") or {}
        if not state or state.get("status") in ("unconfigured", "disabled"):
            return "人体温度真实传感器尚未接入"
        return "采集已启动"

    def values(self, snapshot):
        return self.reader.values(snapshot)

    def tick(self, snapshot, now):
        due = self.context.active_scenes.get(self.scene)
        if due is None or now < due:
            return []
        values = self.values(snapshot)
        sent = self.screen.handle_message({"scene": self.scene, **values}) if values else []
        self.context.active_scenes[self.scene] = now + self.context.interval
        return sent

