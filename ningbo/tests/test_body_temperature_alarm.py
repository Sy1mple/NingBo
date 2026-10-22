"""测温阈值、周期短鸣、停止/故障释放及共享蜂鸣器的行为验证。"""
from copy import deepcopy
from dataclasses import replace
import unittest

from config import DEFAULT_CONFIG
from experiments.body_temperature.jetson.alarm import BodyTemperatureAlarm
from experiments.body_temperature.jetson.feedback import color_for
from hardware.buzzer_feedback import BuzzerFeedback
from scene_router import ScreenDeviceBridge


class Devices:
    def __init__(self):
        self.config = DEFAULT_CONFIG
        self.calls = []
        self.fail = False
        self.state = {
            'sensors': {},
            'actuators': {'buzzer': {'on': False}, 'rgb': {'color': 'off'},
                          'servo': {'initialized': False}, 'stepper': {'initialized': False}},
        }

    def snapshot(self):
        return deepcopy(self.state)

    def control(self, name, **kwargs):
        if self.fail:
            raise OSError('output unavailable')
        self.calls.append((name, kwargs))
        self.state['actuators'].setdefault(name, {}).update(kwargs)
        return kwargs


class Screen:
    def __init__(self):
        self.messages = []

    def handle_message(self, message):
        self.messages.append(message)
        return []


class BodyTemperatureAlarmTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.devices = Devices()
        self.alarm = BodyTemperatureAlarm(self.devices, DEFAULT_CONFIG,
                                           lambda: self.now, lambda: self.now)

    def sample(self, value=31.27, age=0, status='normal'):
        return {'temperature': value, 'sample_time': self.now - age, 'status': status}

    def update(self, value=31.27, age=0, status='normal'):
        self.alarm.observe({'body_temperature': self.sample(value, age, status)})
        self.alarm.flush()

    def test_exact_user_color_boundaries_and_no_upper_45_degree_cutoff(self):
        for temperature, expected in ((21.7, 'blue'), (24.99, 'blue'), (25, 'green'),
                                      (27.99, 'green'), (28, 'green'), (28.01, 'red'),
                                      (31.27, 'red'), (50, 'red'), (380, 'red')):
            with self.subTest(temperature=temperature):
                self.assertEqual(color_for(self.sample(temperature), DEFAULT_CONFIG, self.now), expected)

    def test_no_output_before_start_and_no_sound_at_28_degrees(self):
        self.update()
        self.assertEqual(self.devices.calls, [])
        self.alarm.activate()
        self.update(28)
        self.assertFalse(self.devices.state['actuators']['buzzer']['on'])

    def test_one_short_beep_per_second_without_sleep(self):
        self.alarm.activate()
        for elapsed, expected in ((0, True), (.1, True), (.25, False), (.95, False),
                                  (1.01, True), (1.25, False), (2.01, True)):
            self.now = 100 + elapsed
            self.update()
            self.assertEqual(self.devices.state['actuators']['buzzer']['on'], expected)
            self.assertEqual(color_for(self.sample(), DEFAULT_CONFIG, self.now), 'red')

    def test_cooling_to_threshold_stops_and_reheating_starts_a_fresh_pulse(self):
        self.alarm.activate()
        self.update()
        self.now += .3
        self.update(28)
        self.assertFalse(self.devices.state['actuators']['buzzer']['on'])
        self.update(28.01)
        self.assertTrue(self.devices.state['actuators']['buzzer']['on'])

    def test_faults_stale_and_invalid_values_release_alarm(self):
        for value, age, status in ((31, 6, 'normal'), (31, 0, 'error'),
                                    (float('nan'), 0, 'normal'), (True, 0, 'normal'),
                                    (381, 0, 'normal'), (None, 0, 'normal')):
            with self.subTest(value=value, age=age, status=status):
                self.alarm.activate()
                self.update()
                self.update(value, age, status)
                self.assertFalse(self.devices.state['actuators']['buzzer']['on'])
                self.assertEqual(color_for(self.sample(value, age, status), DEFAULT_CONFIG, self.now), 'blue')

    def test_stop_during_pulse_closes_buzzer_and_failed_close_retries(self):
        self.alarm.activate()
        self.update()
        self.alarm.release()
        self.devices.fail = True
        with self.assertRaises(OSError):
            self.alarm.flush()
        self.devices.fail = False
        self.alarm.flush()
        self.assertFalse(self.devices.state['actuators']['buzzer']['on'])

    def test_bad_pulse_configuration_is_rejected(self):
        DEFAULT_CONFIG.validate()
        for changes in ({'alarm_on_seconds': 1}, {'alarm_period_seconds': 0},
                        {'alarm_on_seconds': float('nan')}, {'alarm_on_seconds': True}):
            cfg = replace(DEFAULT_CONFIG, body_temperature=replace(DEFAULT_CONFIG.body_temperature, **changes))
            with self.assertRaises(ValueError):
                cfg.validate()


class SharedBuzzerTests(unittest.TestCase):
    def setUp(self):
        self.devices = Devices()
        self.feedback = BuzzerFeedback(self.devices)

    def test_body_pause_cannot_silence_distance_smoke_or_access_alarm(self):
        body = self.feedback.source('body_temperature')
        for other_name in ('ultrasonic', 'smoke', 'access'):
            with self.subTest(other=other_name):
                other = self.feedback.source(other_name)
                other.control('buzzer', on=True)
                body.control('buzzer', on=True)
                body.control('buzzer', on=False)
                self.assertTrue(self.devices.state['actuators']['buzzer']['on'])
                other.control('buzzer', on=False)
                self.assertFalse(self.devices.state['actuators']['buzzer']['on'])

    def test_output_failure_retries_and_released_sources_allow_manual_control(self):
        source = self.feedback.source('body_temperature')
        self.devices.fail = True
        with self.assertRaises(OSError):
            source.control('buzzer', on=True)
        self.devices.fail = False
        self.feedback.flush()
        self.assertTrue(self.devices.state['actuators']['buzzer']['on'])
        source.control('buzzer', on=False)
        self.devices.control('buzzer', on=True)
        self.feedback.flush()
        self.assertTrue(self.devices.state['actuators']['buzzer']['on'])

    def test_non_buzzer_control_passes_through_and_reset_is_idle_before_use(self):
        self.feedback.reset()
        self.assertEqual(self.devices.calls, [])
        self.feedback.source('access').control('lock', on=True)
        self.assertEqual(self.devices.calls, [('lock', {'on': True})])

    def test_existing_scene_stop_order_is_preserved(self):
        self.feedback.source('access').control('buzzer', on=True)
        self.feedback.source('ultrasonic').control('buzzer', on=False)
        self.assertFalse(self.devices.state['actuators']['buzzer']['on'])


class BodyTemperatureBridgeTests(unittest.TestCase):
    def test_screen_start_stop_colors_and_shared_alarm_through_actual_router(self):
        now = [100.0]
        devices, screen = Devices(), Screen()
        bridge = ScreenDeviceBridge(screen, devices, clock=lambda: now[0], wall_clock=lambda: now[0])

        def temperature(value):
            devices.state['sensors']['body_temperature'] = {
                'status': 'normal', 'temperature': value, 'sample_time': now[0]}

        temperature(31.27)
        bridge.tick()
        self.assertEqual(devices.calls, [])
        bridge.handle_event('body_temperature.start')
        self.assertEqual(devices.state['actuators']['rgb']['color'], 'red')
        self.assertTrue(devices.state['actuators']['buzzer']['on'])
        now[0] += .3
        bridge.tick()
        self.assertFalse(devices.state['actuators']['buzzer']['on'])
        temperature(26)
        bridge.tick()
        self.assertEqual(devices.state['actuators']['rgb']['color'], 'green')
        temperature(23)
        bridge.tick()
        self.assertEqual(devices.state['actuators']['rgb']['color'], 'blue')
        temperature(31)
        bridge.tick()
        bridge.handle_event('body_temperature.stop')
        self.assertFalse(devices.state['actuators']['buzzer']['on'])
        self.assertEqual(devices.state['actuators']['rgb']['color'], 'off')
        self.assertEqual(screen.messages[-1], {'scene': 'body_temperature', 'temperature': 0})


if __name__ == '__main__':
    unittest.main()
