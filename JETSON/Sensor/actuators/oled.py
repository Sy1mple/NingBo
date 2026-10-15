"""SH1106/SSD1306 OLED，复用设备服务的 I2C 连接。"""


class OledDriver:
    def __init__(self, i2c, config):
        self.i2c = i2c
        self.config = config
        self._initialized = False

    def _command(self, *values):
        self.i2c.bus.write_i2c_block_data(self.config.address, 0x00, list(values))

    def read(self):
        with self.i2c.lock:
            self.i2c.open()
            self._command(0xE3)  # NOP：确认设备应答，不覆盖显示内容
        return {"ok": True, "bus": f"i2c-{self.i2c.bus_number}", "addr": f"0x{self.config.address:02X}"}

    def show_lines(self, lines):
        from PIL import Image, ImageDraw, ImageFont
        cfg = self.config
        try:
            font = ImageFont.truetype(cfg.font_path, cfg.font_size)
        except OSError:
            font = ImageFont.load_default()
        image = Image.new("1", (cfg.width, cfg.height), 0)
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines[:cfg.height // cfg.line_height]):
            draw.text((0, index * cfg.line_height), str(line), font=font, fill=1)
        with self.i2c.lock:
            self.i2c.open()
            if not self._initialized:
                charge_pump = (0xAD, 0x8B) if cfg.driver == "sh1106" else (0x8D, 0x14, 0x20, 0x02)
                self._command(0xAE, 0xA8, cfg.height - 1, 0xD3, 0x00, 0x40,
                              0xA1, 0xC8, 0xDA, 0x12, 0x81, 0x80,
                              *charge_pump, 0xA6, 0xA4, 0xAF)
                self._initialized = True
            column_offset = 2 if cfg.driver == "sh1106" else 0
            for page in range(cfg.height // 8):
                self._command(0xB0 | page, column_offset & 0x0F, 0x10 | (column_offset >> 4))
                row = [sum(int(bool(image.getpixel((x, page * 8 + bit)))) << bit for bit in range(8)) for x in range(cfg.width)]
                for index in range(0, len(row), 16):
                    self.i2c.bus.write_i2c_block_data(cfg.address, 0x40, row[index:index + 16])
        return True

    def clear(self):
        return self.show_lines([])
