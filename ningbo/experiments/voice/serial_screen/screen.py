"""voice: scene-owned button decoding and display formatting."""
from .addresses import BUTTON_EVENT_MAP, INPUT_EVENT_MAP


def decode_event(address, value, frame=b""):
    name = BUTTON_EVENT_MAP.get((address, value), INPUT_EVENT_MAP.get(address))
    if name is None:
        return None
    return {"event": name, "address": address, "value": value,
            "hex": frame.hex(" ").upper()}


def voice_text_update(text, config):
    import screen_registry as addresses
    if (isinstance(config.text_words, bool) or not isinstance(config.text_words, int)
            or not 2 <= config.text_words <= 126 or config.text_encoding not in ("gbk", "gb2312", "utf-16-be")):
        raise ValueError("语音文本编码或缓冲区长度无效")
    if not isinstance(text, str):
        raise ValueError("语音识别文本必须是字符串")
    if any(ord(char) < 32 for char in text):
        raise ValueError("语音识别文本不能含控制字符")
    address = addresses.VOICE_RECOGNIZED_TEXT
    if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
        raise ValueError("语音文本 VP 无效")
    end = address + config.text_words
    occupied = set(addresses.ADDRESS_MAP.values()) - {address}
    occupied.update(vp for vp, _ in addresses.BUTTON_EVENT_MAP)
    occupied.update(vp for vp in addresses.STATUS_RESULT_ADDRESS_MAP.values() if vp is not None)
    occupied.update(vp for vp in addresses.STATUS_META_ADDRESS_MAP.values() if vp is not None)
    if end > 0x10000 or any(address <= vp < end for vp in occupied):
        raise ValueError("语音文本缓冲区与其他屏幕 VP 冲突")
    encoded = bytearray()
    visible = []
    capacity = config.text_words * 2
    for char in text:
        try:
            block = char.encode(config.text_encoding)
        except UnicodeEncodeError as exc:
            raise ValueError(f"屏幕 {config.text_encoding} 字库无法显示字符: {char}") from exc
        if len(encoded) + len(block) > capacity - 2:
            break
        encoded.extend(block)
        visible.append(char)
    if len(encoded) % 2:
        encoded.append(0x20)
    encoded.extend(b"\xff\xff")
    encoded.extend(b"\xff" * (capacity - len(encoded)))
    return {"name": "voice.recognized_text", "address": address, "data": bytes(encoded),
            "text": "".join(visible), "original_text": text}


def build_updates(data, voice_config):
    if "recognized_text" not in data:
        raise ValueError("voice 消息缺少 recognized_text")
    return [voice_text_update(data["recognized_text"], voice_config)]
