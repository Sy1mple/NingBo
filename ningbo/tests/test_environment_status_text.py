"""Chinese status packets and adjacent VP safety, without physical I/O."""
import unittest
from unittest.mock import patch

from hardware.screen.frames import ScreenEventParser
from hardware.screen.sender import ScreenSender
from screen_service import MessageParser
from system.diagnostics.serial_screen import addresses
from system.diagnostics.serial_screen.screen import validate_status_result_addresses


class EnvironmentStatusTextTests(unittest.TestCase):
    def setUp(self):
        self.parser = MessageParser()

    def updates(self, statuses):
        return self.parser.parse({"scene": "diagnostics", "statuses": statuses})

    def test_normal_and_error_use_one_glyph_at_original_adjacent_vps(self):
        updates = self.updates({"dht11": "normal", "bh1750": "error"})
        expected = [(0x6000, "好"), (0x6001, "坏")]
        regions = []
        for item, (address, text) in zip(updates, expected):
            self.assertEqual(item["address"], address)
            self.assertEqual(item["data"], text.encode("gb2312"))
            frame = ScreenSender.build_write_bytes_frame(address, item["data"])
            self.assertEqual(frame[:6], bytes((0x5A, 0xA5, 5, 0x82, address >> 8, address & 255)))
            self.assertEqual(frame[6:], item["data"])
            regions.append(set(range(address, address + len(item["data"]) // 2)))
        self.assertFalse(regions[0] & regions[1])

    def test_checking_then_final_text_replaces_exactly_one_word(self):
        memory = {}
        for status, expected in (("checking", "检"), ("normal", "好"), ("error", "坏")):
            item = self.updates({"dht11": status})[0]
            for offset, value in enumerate(item["data"]):
                memory[2 * item["address"] + offset] = value
            data = bytes(memory[2 * item["address"] + offset] for offset in range(2))
            self.assertEqual(data.decode("gb2312"), expected)
            self.assertEqual(len(data), 2)

    def test_all_lifecycle_states_fit_the_control(self):
        for status in addresses.STATUS_CODE_MAP:
            with self.subTest(status=status):
                item = self.updates({"bh1750": status})[0]
                self.assertEqual(len(item["data"]), 2)
                self.assertEqual(len(item["data"].decode("gb2312")), 1)

    def test_other_devices_keep_their_existing_word_encoding(self):
        item = self.updates({"body_temperature": "normal"})[0]
        self.assertEqual(item["address"], 0x6003)
        self.assertEqual(item["value"], 1)
        self.assertNotIn("data", item)

    def test_duplicate_status_vp_is_rejected(self):
        changed = dict(addresses.STATUS_RESULT_ADDRESS_MAP, bh1750=0x6000)
        with patch.dict(addresses.STATUS_RESULT_ADDRESS_MAP, changed, clear=True):
            with self.assertRaisesRegex(ValueError, "冲突"):
                validate_status_result_addresses()

    def test_last_word_of_vp_memory_can_hold_one_glyph(self):
        with patch.dict(addresses.STATUS_RESULT_ADDRESS_MAP, dht11=0xFFFF):
            self.assertTrue(validate_status_result_addresses())

    def test_light_text_does_not_erase_smoke_status_at_next_vp(self):
        memory = {0x6002: b"\x00\x02"}
        for item in self.updates({"dht11": "normal", "bh1750": "normal"}):
            self.assertEqual(len(item["data"]), 2)
            memory[item["address"]] = item["data"]
        self.assertEqual(memory[0x6000], "好".encode("gb2312"))
        self.assertEqual(memory[0x6001], "好".encode("gb2312"))
        self.assertEqual(memory[0x6002], b"\x00\x02")

    def test_original_sensor_button_still_decodes(self):
        for frame in ('5aa506839001018001', '5aa5058290018001'):
            event = ScreenEventParser().feed(bytes.fromhex(frame))[0]
            self.assertEqual(event['event'], 'diagnostics.sensors')


if __name__ == '__main__':
    unittest.main()
