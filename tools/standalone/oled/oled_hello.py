from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont

serial = i2c(port=8, address=0x3C)
device = ssd1306(serial)

try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
except Exception:
    font = ImageFont.load_default()

image = Image.new("1", (device.width, device.height))
draw = ImageDraw.Draw(image)
draw.rectangle((0, 0, device.width - 1, device.height - 1), outline=255, fill=0)
draw.text((8, 8), "Hello", font=font, fill=255)
draw.text((8, 36), "World", font=font, fill=255)
device.display(image)

print("device:", device.width, "x", device.height)
print("HELLO WORLD displayed on OLED (i2c-8 addr 0x3C)")