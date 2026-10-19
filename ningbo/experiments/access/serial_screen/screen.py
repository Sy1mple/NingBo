"""access: scene-owned button decoding and display formatting."""
from .addresses import BUTTON_EVENT_MAP, INPUT_EVENT_MAP


def decode_event(address, value, frame=b""):
    name = BUTTON_EVENT_MAP.get((address, value), INPUT_EVENT_MAP.get(address))
    if name is None:
        return None
    return {"event": name, "address": address, "value": value,
            "hex": frame.hex(" ").upper()}


FIELDS = {}

def build_updates(data, voice_config=None):
    raise ValueError("No display fields are defined for access")
