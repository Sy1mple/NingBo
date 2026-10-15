import time
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306, sh1106
from PIL import Image, ImageDraw, ImageFont

serial = i2c(port=8, address=0x3C)
print("i2c bus=8 addr=0x3C ACK ok")

# 1) SH1106 solid white — 0.96" modules are often SH1106
print("TEST1: sh1106 full white (watch the screen)")
dev = sh1106(serial)
print("sh1106 size:", dev.width, "x", dev.height)
img = Image.new("1", (dev.width, dev.height), 255)
dev.display(img)
time.sleep(4)

print("TEST2: sh1106 Hello World")
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
except Exception:
    font = ImageFont.load_default()
img = Image.new("1", (dev.width, dev.height), 0)
d = ImageDraw.Draw(img)
d.text((6, 6), "Hello", font=font, fill=255)
d.text((6, 34), "World", font=font, fill=255)
dev.display(img)
time.sleep(3)

print("TEST3: ssd1306 full white fallback")
dev2 = ssd1306(serial)
img = Image.new("1", (dev2.width, dev2.height), 255)
dev2.display(img)
print("done")