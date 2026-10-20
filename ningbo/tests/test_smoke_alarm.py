import unittest
from unittest.mock import Mock

from config import DEFAULT_CONFIG
from experiments.smoke.jetson.alarm import SmokeAlarm, color_for
from experiments.smoke.jetson.readings import SnapshotReader, display_percent


class SmokeAlarmTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.devices = Mock()
        self.alarm = SmokeAlarm(self.devices, DEFAULT_CONFIG,
                                lambda: self.now, lambda: self.now)

    def sample(self, value=560, age=0):
        return {"protocol": "ffaa", "status": "normal", "concentration": value,
                "sample_time": self.now - age}

    def update(self, value=560, age=0):
        self.alarm.observe({"smoke": self.sample(value, age)})
        self.alarm.flush()

    def test_screen_and_alarm_use_same_percentage(self):
        reader = SnapshotReader(DEFAULT_CONFIG, lambda: self.now)
        self.alarm.activate()
        for raw, expected in [(0, 0), (503, 72), (550, 79), (556, 79),
                              (557, 80), (560, 80), (700, 100), (65535, 100)]:
            state = self.sample(raw)
            self.assertEqual(display_percent(state, DEFAULT_CONFIG, self.now), expected)
            self.assertEqual(reader.values({"sensors": {"smoke": state}}), {"value": expected})
            self.update(raw)
            self.assertEqual(self.alarm.desired, expected >= 80)
            self.assertEqual(color_for(state, DEFAULT_CONFIG, self.now),
                             "red" if expected >= 80 else "green")

    def test_no_alarm_before_screen_start(self):
        self.update()
        self.devices.control.assert_not_called()

    def test_pulse_every_three_seconds_and_red_between_pulses(self):
        self.alarm.activate()
        for elapsed, expected in [(0, True), (.5, False), (2.9, False), (3.01, True), (3.5, False)]:
            self.now = 100 + elapsed
            self.update()
            self.assertEqual(self.alarm.desired, expected)
            self.assertEqual(color_for(self.sample(), DEFAULT_CONFIG, self.now), "red")

    def test_threshold_clear_and_reentry(self):
        self.alarm.activate()
        self.update()
        self.update(550)
        self.assertFalse(self.alarm.desired)
        self.assertEqual(color_for(self.sample(550), DEFAULT_CONFIG, self.now), "green")
        self.update()
        self.assertTrue(self.alarm.desired)

    def test_stale_reading_does_not_continue_alarm(self):
        self.alarm.activate()
        self.update()
        self.update(age=6)
        self.assertFalse(self.alarm.desired)
        self.assertEqual(color_for(self.sample(age=6), DEFAULT_CONFIG, self.now),
                         DEFAULT_CONFIG.rgb_feedback.waiting_color)

    def test_stop_turns_buzzer_off(self):
        self.alarm.activate()
        self.update()
        self.alarm.release()
        self.alarm.flush()
        self.devices.control.assert_called_with("buzzer", on=False)


if __name__ == "__main__":
    unittest.main()
