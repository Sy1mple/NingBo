"""Default wiring must stay consistent when changing feedback interpretation."""
import unittest
from config import Config


class AdcMappingTests(unittest.TestCase):
    def test_default_voltage_and_current_paths_use_the_confirmed_device_locations(self):
        config = Config().validate()
        expected = {'servo': (0x48, 0), 'lock': (0x48, 1), 'fan': (0x48, 2),
                    'rgb': (0x48, 3), 'buzzer': (0x49, 0), 'stepper': (0x49, 1)}
        for name, location in expected.items():
            with self.subTest(name=name):
                self.assertEqual((config.ads1115.feedback_addresses.get(name, config.ads1115.address),
                                  config.ads1115.feedback_channels[name]), location)
                current = config.current.channels[name]
                self.assertEqual((current.address, current.channel), location)

    def test_current_conversion_stays_disabled_until_calibrated(self):
        config = Config()
        self.assertFalse(config.current.enabled)
        self.assertNotIn('lock', config.ads1115.feedback_voltage_limits)

    def test_config_instances_do_not_share_mutable_channel_maps(self):
        first, second = Config(), Config()
        first.ads1115.feedback_channels['lock'] = 3
        first.current.channels.pop('lock')
        self.assertEqual(second.ads1115.feedback_channels['lock'], 1)
        self.assertEqual(second.current.channels['lock'].channel, 1)


if __name__ == '__main__':
    unittest.main()
