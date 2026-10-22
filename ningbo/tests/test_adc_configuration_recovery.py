"""Configuration loss must be retried without accepting data from the wrong channel."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import DEFAULT_CONFIG
from hardware.adc.voltage import Ads1115Monitor


class AdcConfigurationRecoveryTests(unittest.TestCase):
    def monitor(self, configs):
        configs = iter(configs)
        bus = SimpleNamespace(write_i2c_block_data=Mock())
        def read(address, register, count):
            if register == 1:
                value = next(configs)
                return [value >> 8, value & 255]
            return [0, 32]
        bus.read_i2c_block_data = Mock(side_effect=read)
        resource = SimpleNamespace(lock=threading.RLock(), open=Mock(), bus=bus)
        return Ads1115Monitor(resource, DEFAULT_CONFIG.ads1115), bus

    def test_reset_config_is_rewritten_and_conversion_waits_for_correct_config(self):
        monitor, bus = self.monitor([0x8583, 0x7B83, 0xFB83])
        with patch('hardware.adc.voltage.time.sleep'):
            sample = monitor.read_channel(3, address=0x48, full_scale_volts=.256)
        self.assertEqual(sample['raw'], 32)
        self.assertEqual(sample['channel'], 3)
        self.assertEqual(bus.write_i2c_block_data.call_count, 2)
        self.assertEqual(bus.read_i2c_block_data.call_args.args, (0x48, 0, 2))

    def test_persistent_wrong_config_never_reads_conversion_register(self):
        monitor, bus = self.monitor([0x8583] * 3)
        with patch('hardware.adc.voltage.time.sleep'):
            with self.assertRaisesRegex(IOError, '配置读回不一致'):
                monitor.read_channel(3, full_scale_volts=.256)
        self.assertEqual(bus.write_i2c_block_data.call_count, 3)
        self.assertTrue(all(call.args[1] == 1 for call in bus.read_i2c_block_data.call_args_list))

    def test_recovery_does_not_reset_the_original_conversion_deadline(self):
        monitor, bus = self.monitor([0x8583])
        with patch('hardware.adc.voltage.time.monotonic', side_effect=[100, 101]):
            with self.assertRaisesRegex(IOError, '配置读回不一致'):
                monitor.read_channel(3, full_scale_volts=.256)
        self.assertEqual(bus.write_i2c_block_data.call_count, 1)


if __name__ == '__main__':
    unittest.main()
