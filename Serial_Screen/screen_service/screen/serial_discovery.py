"""Identify the HMI by two read-only VP replies, never by a USB index."""
import logging
from pathlib import Path
import sys
import time

LOGGER = logging.getLogger(__name__)
PROBE_ADDRESSES = (0x1000, 0x1030)


def _reply(port, address, pending, timeout):
    query = bytes((0x5A, 0xA5, 4, 0x83, address >> 8, address & 255, 1))
    if port.write(query) != len(query):
        raise OSError('串口屏探测请求未完整写入')
    prefix = bytes((0x5A, 0xA5, 6, 0x83, address >> 8, address & 255, 1))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = port.read(min(port.in_waiting or 1, 4096))
        pending.extend(data)
        index = pending.find(prefix)
        if index >= 0 and len(pending) >= index + 9:
            del pending[index:index + 9]
            return True
        if len(pending) > 4096:
            del pending[:-4096]
    return False


def discover_screen(config, excluded_ports=(), port_infos=None, serial_factory=None):
    """Return the verified open port and bytes received during probing.

    Enumerate all ports, probe USB serial ports only, and exclude other configured
    serial peripherals. Multiple answering screens require an explicit --port.
    """
    if port_infos is None:
        from serial.tools import list_ports
        port_infos = list_ports.comports()
    if serial_factory is None:
        import serial
        serial_factory = serial.Serial
    excluded = {str(Path(p).resolve()) for p in excluded_ports if p and p != 'auto'}
    candidates = {}
    for info in port_infos:
        name = info.device
        real = str(Path(name).resolve())
        usb = getattr(info, 'vid', None) is not None or name.startswith(('/dev/ttyUSB', '/dev/ttyACM'))
        if usb and real not in excluded:
            candidates.setdefault(real, name)
    LOGGER.info('串口屏自动扫描 USB 串口：%s', ', '.join(candidates.values()) or '无')
    matches, errors = [], []
    selected = None
    try:
        for name in sorted(candidates.values()):
            port = None
            try:
                kwargs = dict(port=None, baudrate=config.baudrate, bytesize=config.bytesize,
                              parity=config.parity, stopbits=config.stopbits,
                              timeout=0.03, write_timeout=config.write_timeout)
                if sys.platform.startswith('linux'):
                    kwargs['exclusive'] = True
                port = serial_factory(**kwargs)
                # Set modem control before opening so probing does not assert DTR/RTS.
                port.dtr = False
                port.rts = False
                port.port = name
                port.open()
                pending = bytearray()
                matched = False
                for _ in range(config.auto_probe_attempts):
                    if all(_reply(port, address, pending, config.auto_probe_timeout)
                           for address in PROBE_ADDRESSES):
                        matched = True
                        break
                if matched:
                    port.timeout = config.read_timeout
                    matches.append((port, bytes(pending)))
                    port = None
                    LOGGER.info('串口屏协议应答通过：%s', name)
                else:
                    errors.append(name + ': 无有效屏幕应答')
            except (OSError, ValueError) as exc:
                errors.append(name + ': ' + str(exc))
            finally:
                if port is not None:
                    port.close()
        if len(matches) != 1:
            names = ', '.join(port.port for port, _ in matches)
            if matches:
                raise OSError('发现多块串口屏：' + names + '；请使用 --port 指定')
            raise OSError('未发现有协议应答的串口屏；' + '; '.join(errors or ['无可用 USB 串口']))
        selected, pending = matches[0]
        return selected, pending
    finally:
        for port, _ in matches:
            if port is not selected:
                port.close()
