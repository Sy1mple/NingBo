import time
from smbus2 import SMBus, i2c_msg

BUS, ADDR = 8, 0x3C
bus = SMBus(BUS)

def cmd(*bytes_):
    for i in range(0, len(bytes_), 16):
        chunk = list(bytes_[i:i+16])
        msg = i2c_msg.write(ADDR, [0x00] + chunk)
        bus.i2c_rdwr(msg)

def data(buf):
    for i in range(0, len(buf), 16):
        chunk = list(buf[i:i+16])
        msg = i2c_msg.write(ADDR, [0x40] + chunk)
        bus.i2c_rdwr(msg)

print("scan confirm:")
import subprocess
print(subprocess.check_output(["i2cdetect","-y","-r","8"], text=True))

# ---- SSD1306 raw init (charge pump ON) ----
print("RAW SSD1306 init + full white")
cmd(0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
    0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12,
    0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF)
data([0xFF] * 1024)
time.sleep(3)

# ---- SH1106 raw init ----
print("RAW SH1106 init + full white")
cmd(0xAE, 0x02, 0x10, 0x40, 0x81, 0x80, 0xA1, 0xC8,
    0xA6, 0xA8, 0x3F, 0xD5, 0x80, 0xD3, 0x00, 0xAD,
    0x8B, 0x20, 0x00, 0xA4, 0xAF)
# SH1106: set page/col then data
for page in range(8):
    cmd(0xB0 | page, 0x00, 0x10)
    data([0xFF] * 128)
time.sleep(3)

print("raw sequences sent, done")