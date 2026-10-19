"""environment: the authoritative VP and button definitions for this screen page."""

ENV_TEMPERATURE = 0x1000

ENV_HUMIDITY = 0x1010

ENV_LIGHT = 0x1020

ENV_DETECT_START_KEY_ADDR = 0x2000

ENV_DETECT_START_BACK_VALUE = 0x4000

ENV_DETECT_STOP_KEY_ADDR = 0x2010

ENV_DETECT_STOP_BACK_VALUE = 0x4010

ADDRESS_MAP = {
    'environment.temperature': ENV_TEMPERATURE,
    'environment.humidity': ENV_HUMIDITY,
    'environment.light': ENV_LIGHT,
}

BUTTON_EVENT_MAP = {
    (ENV_DETECT_START_KEY_ADDR, ENV_DETECT_START_BACK_VALUE): 'environment.start',
    (ENV_DETECT_STOP_KEY_ADDR, ENV_DETECT_STOP_BACK_VALUE): 'environment.stop',
}

INPUT_EVENT_MAP = {
}

# Existing adjacent text VPs: each result writes exactly one GB2312 glyph.
DHT11_STATUS_RESULT_ADDR = 0x6000
LIGHT_STATUS_RESULT_ADDR = 0x6001
STATUS_RESULT_TEXT_BYTES = 2
STATUS_RESULT_TEXT_TARGETS = ("dht11", "bh1750")
