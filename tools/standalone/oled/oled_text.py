import time
from smbus2 import SMBus, i2c_msg
from PIL import Image, ImageDraw, ImageFont

BUS, ADDR = 8, 0x3C
bus = SMBus(BUS)

def cmd(*bytes_):
    msg = i2c_msg.write(ADDR, [0x00] + list(bytes_))
    bus.i2c_rdwr(msg)

def data(buf):
    for i in range(0, len(buf), 16):
        msg = i2c_msg.write(ADDR, [0x40] + list(buf[i:i+16]))
        bus.i2c_rdwr(msg)

# render text
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
except Exception:
    font = ImageFont.load_default()

img = Image.new("1", (128, 64), 0)
d = ImageDraw.Draw(img)
d.text((8, 6), "Hello", font=font, fill=255)
d.text((8, 34), "World", font=font, fill=255)

# SH1106 init (same sequence that just lit the screen)
cmd(0xAE, 0x02, 0x10, 0x40, 0x81, 0x80, 0xA1, 0xC8,
    0xA6, 0xA8, 0x3F, 0xD5, 0x80, 0xD3, 0x00, 0xAD,
    0x8B, 0x20, 0x00, 0xA4, 0xAF)

# page addressing write (SH1106)
for page in range(8):
    cmd(0xB0 | page, 0x00, 0x10)
    row = []
    for x in range(128):
        byte = 0
        for bit in range(8):
            if img.getpixel((x, page * 8 + bit)):
                byte |= (1 << bit)
        row.append(byte)
    data(row)

print("SH1106 raw text sent: Hello / World")