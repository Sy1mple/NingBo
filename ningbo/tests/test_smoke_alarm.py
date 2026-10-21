import unittest
from unittest.mock import Mock
from dataclasses import replace

from config import DEFAULT_CONFIG
from experiments.smoke.jetson.alarm import SmokeAlarm, color_for
from experiments.smoke.jetson.readings import SnapshotReader, display_percent


class SmokeAlarmTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.config = replace(DEFAULT_CONFIG, smoke=replace(DEFAULT_CONFIG.smoke,
            display_baseline_raw=0, display_normal_value=0, display_full_scale_raw=700))
        self.devices = Mock()
        self.alarm = SmokeAlarm(self.devices, self.config,
                                lambda: self.now, lambda: self.now)

    def sample(self, value=560, age=0):
        return {"protocol": "ffaa", "status": "normal", "concentration": value,
                "sample_time": self.now - age}

    def update(self, value=560, age=0):
        self.alarm.observe({"smoke": self.sample(value, age)})
        self.alarm.flush()

    def test_screen_and_alarm_use_same_percentage(self):
        reader = SnapshotReader(self.config, lambda: self.now)
        self.alarm.activate()
        for raw, expected in [(0, 0), (503, 72), (550, 79), (556, 79),
                              (557, 80), (560, 80), (700, 100), (65535, 100)]:
            state = self.sample(raw)
            self.assertEqual(display_percent(state, self.config, self.now), expected)
            self.assertEqual(reader.values({"sensors": {"smoke": state}}), {"value": expected})
            self.update(raw)
            self.assertEqual(self.alarm.desired, expected >= 80)
            self.assertEqual(color_for(state, self.config, self.now),
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
            self.assertEqual(color_for(self.sample(), self.config, self.now), "red")

    def test_threshold_clear_and_reentry(self):
        self.alarm.activate()
        self.update()
        self.update(550)
        self.assertFalse(self.alarm.desired)
        self.assertEqual(color_for(self.sample(550), self.config, self.now), "green")
        self.update()
        self.assertTrue(self.alarm.desired)

    def test_stale_reading_does_not_continue_alarm(self):
        self.alarm.activate()
        self.update()
        self.update(age=6)
        self.assertFalse(self.alarm.desired)
        self.assertEqual(color_for(self.sample(age=6), self.config, self.now),
                         DEFAULT_CONFIG.rgb_feedback.waiting_color)

    def test_stop_turns_buzzer_off(self):
        self.alarm.activate()
        self.update()
        self.alarm.release()
        self.alarm.flush()
        self.devices.control.assert_called_with("buzzer", on=False)

    def test_measured_normal_air_has_display_headroom_without_alarm(self):
        reader = SnapshotReader(DEFAULT_CONFIG, lambda: self.now)
        alarm = SmokeAlarm(self.devices, DEFAULT_CONFIG, lambda: self.now, lambda: self.now)
        alarm.activate()
        for raw in (1644, 1655, 1695, 1716):
            state = self.sample(raw)
            displayed = reader.values({'sensors': {'smoke': state}})['value']
            self.assertGreaterEqual(displayed, 19)
            self.assertLessEqual(displayed, 23)
            alarm.observe({'smoke': state})
            self.assertFalse(alarm.desired)
            self.assertEqual(color_for(state, DEFAULT_CONFIG, self.now), 'green')
            self.assertEqual(state['concentration'], raw)

    def test_relative_display_and_alarm_use_the_same_fixed_reference(self):
        alarm = SmokeAlarm(self.devices, DEFAULT_CONFIG, lambda: self.now, lambda: self.now)
        alarm.activate()
        for raw, expected in ((0, 0), (1655, 20), (2482, 60), (2800, 75),
                              (3000, 85), (3310, 100), (65535, 100)):
            state = self.sample(raw)
            self.assertEqual(display_percent(state, DEFAULT_CONFIG, self.now), expected)
            alarm.observe({'smoke': state})
            self.assertEqual(alarm.desired, expected >= DEFAULT_CONFIG.smoke.alarm_threshold)
        alarm.release()
        alarm.activate()
        self.assertEqual(display_percent(self.sample(1655), DEFAULT_CONFIG, self.now), 20)

    def test_invalid_relative_ranges_are_rejected(self):
        from experiments.smoke.jetson.config import validate
        for changes in ({'display_baseline_raw': -1}, {'display_baseline_raw': 3310},
                        {'display_normal_value': 80}, {'display_normal_value': True}):
            cfg = replace(DEFAULT_CONFIG, smoke=replace(DEFAULT_CONFIG.smoke, **changes))
            with self.assertRaises(ValueError):
                validate(cfg)


if __name__ == "__main__":
    unittest.main()
