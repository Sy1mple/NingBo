"""从 Config 动态派生排针说明，不再次定义设备引脚。"""
from .Config import DEFAULT_CONFIG


def board_pinmap(config=None):
    config = config or DEFAULT_CONFIG
    used = config.gpio_devices()
    labels = {"dht11": "DHT11", "ultrasonic.trig": "超声 TRIG", "ultrasonic.echo": "超声 ECHO",
              "servo": "舵机信号", "buzzer": "蜂鸣器", "lock": "门锁继电器", "fan": "风扇继电器"}
    labels.update({f"rgb.{i}": f"RGB {name}" for i, name in enumerate(("R", "G", "B"))})
    labels.update({f"stepper.{i}": f"步进 IN{i+1}" for i in range(4)})
    used = {pin: labels.get(name, name) for pin, name in used.items()}
    used.update(dict(zip(config.i2c_pins, ("I2C SDA", "I2C SCL"))))
    used.update(dict(zip(config.uart_pins, ("UART TX", "UART RX"))))
    supplies = {1: "3.3V", 17: "3.3V", 2: "5V", 4: "5V"}
    grounds = {6, 9, 14, 20, 25, 30, 34, 39}
    return [{"pin": pin, "label": supplies.get(pin, "GND" if pin in grounds else used.get(pin, "未分配/复用待核")),
             "kind": ("p3v3" if supplies.get(pin) == "3.3V" else "p5v") if pin in supplies else "gnd" if pin in grounds else "used" if pin in used else "spare",
             "side": "L" if pin % 2 else "R"} for pin in range(1, 41)]
