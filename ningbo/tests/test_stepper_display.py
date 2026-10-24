"""真实按钮解析、桥接、GPIO 驱动、格式化和串口帧；无物理电机/串口。"""
from dataclasses import replace
import unittest
from unittest.mock import patch

from application import NingBoApplication
from config import DEFAULT_CONFIG
from device_manager import DeviceManager
from device_service import DeviceService
from screen_service import ScreenService
from hardware.screen.sender import ScreenSender
from experiments.stepper.serial_screen import addresses, screen as formatter
from test_startup_gpio import GpioTrace


class Serial:
    is_open = True
    def __init__(self):
        self.input, self.frames, self.fail_writes = b'', [], 0
    @property
    def in_waiting(self): return len(self.input)
    def read(self, size):
        result, self.input = self.input[:size], self.input[size:]
        return result
    def write(self, frame):
        if self.fail_writes:
            self.fail_writes -= 1
            return 0
        self.frames.append(frame)
        return len(frame)
    def flush(self): pass
    def reset_input_buffer(self): self.input = b''
    def close(self): self.is_open = False


class StepperDisplayTests(unittest.TestCase):
    def setUp(self):
        config = replace(DEFAULT_CONFIG, current=replace(DEFAULT_CONFIG.current, enabled=False),
                         ads1115=replace(DEFAULT_CONFIG.ads1115, enabled=False))
        self.gpio = GpioTrace()
        manager = DeviceManager(config=config, gpio=self.gpio, sensor_drivers={})
        self.driver = manager._outputs['stepper']
        # 手动推进真实驱动的相序，以便精确检查步数，避免调度线程影响断言。
        def initialize():
            self.driver.prepare_idle()
        self.driver.start = initialize
        self.devices = DeviceService(manager=manager)
        self.serial = Serial()
        screen = ScreenService()
        screen.sender.ser = self.serial
        self.app = NingBoApplication(config=config, devices=self.devices, screen=screen,
                                    access_handler=lambda event: None, voice_handler=lambda event: None)
        self.now = 10.0
        self.app.bridge.clock = lambda: self.now
        self.addCleanup(self.app.stop)
        self.app.start()
        self.assertEqual(self.gpio.levels, {29: 0, 31: 0, 12: 0, 35: 0, 38: 0, 40: 1, 13: 0, 16: 0, 18: 0, 22: 0})
        self.gpio.calls.clear()
        self.bridge = self.app.bridge.controls.stepper

    def press(self, start=True):
        address, value = (0x3060,0x5060) if start else (0x3070,0x5070)
        self.serial.input += bytes([0x5a,0xa5,6,0x83,address>>8,address&255,1,value>>8,value&255])
        return self.app.process_once()

    def pair(self, direction, steps):
        return [ScreenSender.build_write_bytes_frame(addresses.STEPPER_DIRECTION,
                    direction.encode('gb2312') + b'\xff\xff'),
                ScreenSender.build_write_frame(addresses.STEPPER_NUMBER, steps)]

    def advance(self, steps):
        for _ in range(steps): self.driver.step_once()
        self.now += .21
        self.app.process_once()

    def test_idle_never_initializes_motor_or_sends_display(self):
        for _ in range(5): self.app.process_once()
        self.assertEqual(self.gpio.calls, [])
        self.assertEqual(self.serial.frames, [])

    def test_start_button_sends_chinese_direction_and_numeric_steps(self):
        result = self.press()
        self.assertEqual(result[0]['event'],'stepper.start')
        self.assertTrue(result[0]['handled'])
        self.assertEqual(self.serial.frames,self.pair('正转',512))
        self.assertEqual(self.driver.snapshot()['dir'],'fwd')
        self.assertEqual(len(result[0]['sent']),2)

    def test_running_refreshes_remaining_steps_without_flooding_unchanged_values(self):
        self.press()
        for _ in range(5): self.app.process_once()
        self.assertEqual(len(self.serial.frames),2)
        self.advance(7)
        self.assertEqual(self.serial.frames[-2:],self.pair('正转',505))
        self.assertEqual(len(self.serial.frames),4)

    def test_stop_button_stops_motor_and_sends_stopped_zero(self):
        self.press()
        self.advance(3)
        self.press(start=False)
        self.assertEqual(self.serial.frames[-2:],self.pair('停止',0))
        self.assertFalse(self.driver.snapshot()['running'])
        self.assertTrue(all(self.gpio.levels[pin]==0 for pin in self.app.config.stepper.pins))
        size = len(self.serial.frames)
        self.advance(0)
        self.assertEqual(len(self.serial.frames),size)

    def test_natural_completion_sends_stopped_zero(self):
        self.press()
        self.advance(512)
        self.assertEqual(self.serial.frames[-2:],self.pair('停止',0))
        self.assertFalse(self.bridge._display_active)

    def test_reverse_display_comes_from_real_driver_state(self):
        self.bridge.handle_event({'event':'stepper.direction','value':1})
        self.press()
        self.assertEqual(self.serial.frames[-2:],self.pair('反转',512))
        self.advance(1)
        self.assertEqual(self.serial.frames[-2:],self.pair('反转',511))

    def test_repeated_start_does_not_restart_step_counter(self):
        self.press()
        self.advance(5)
        self.press()
        self.assertEqual(self.driver.snapshot()['n'],507)
        self.assertEqual(len(self.serial.frames),4)

    def test_display_echo_cannot_change_next_motor_command(self):
        self.serial.input += ScreenSender.build_write_frame(0x1062,0xbeFD)
        self.serial.input += ScreenSender.build_write_frame(0x1063,123)
        self.app.process_once()
        self.assertEqual(self.bridge.parameters,{})
        self.press()
        self.assertEqual(self.driver.snapshot()['n'],512)

    def test_start_write_failure_stops_motor_and_retries_stopped_display(self):
        self.serial.fail_writes = 1
        with self.assertLogs('ningbo',level='ERROR'):
            self.press()
        self.assertFalse(self.driver.snapshot()['running'])
        self.assertEqual(self.serial.frames[-2:],self.pair('停止',0))

    def test_refresh_failure_is_retried_next_loop(self):
        self.press()
        self.driver.step_once()
        self.serial.fail_writes = 1
        self.now += .21
        with self.assertLogs('scene_router',level='ERROR'):
            self.app.process_once()
        self.assertTrue(self.driver.snapshot()['running'])
        self.app.process_once()
        self.assertEqual(self.serial.frames[-2:],self.pair('正转',511))

    def test_legacy_text_address_overlap_is_reported(self):
        formatter._warned_layouts.clear()
        with patch.object(addresses, 'STEPPER_DIRECTION', 0x1062):
            with self.assertLogs(formatter.LOGGER, level='WARNING') as logs:
                self.press()
        self.assertIn('stepper.steps=0x1063', logs.output[0])
        self.assertIn('voice.recognized_text=0x1064', logs.output[0])

    def test_direction_number_and_voice_memory_remain_independent(self):
        self.assertEqual(addresses.STEPPER_DIRECTION, 0x1300)
        self.assertEqual(addresses.STEPPER_NUMBER, 0x1063)
        memory = {}
        def write(frame):
            begin = int.from_bytes(frame[4:6], 'big')
            for offset in range(0, len(frame[6:]), 2):
                memory[begin + offset // 2] = frame[6+offset:8+offset]
        for direction in ('正转', '反转', '停止'):
            for steps in (512, 37, 0):
                for frame in self.pair(direction, steps):
                    write(frame)
                # Reproduce a voice update after the stepper update.
                voice = '语音识别'.encode('gb2312') + b'\xff\xff' * 28
                write(ScreenSender.build_write_bytes_frame(0x1064, voice))
                self.assertEqual(memory[0x1300] + memory[0x1301], direction.encode('gb2312'))
                self.assertEqual(memory[0x1302], b'\xff\xff')
                self.assertEqual(memory[0x1063], steps.to_bytes(2, 'big'))
                self.assertEqual(memory[0x1064] + memory[0x1065], '语音'.encode('gb2312'))


if __name__ == '__main__': unittest.main()
