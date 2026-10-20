"""New project helper path, native failures, retries, cancellation and valid readings."""
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
import threading
import unittest
from unittest.mock import patch

from config import DEFAULT_CONFIG
from paths import PROJECT_ROOT
from experiments.environment.jetson.dht11 import Dht11Driver


class Dht11NativeTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(DEFAULT_CONFIG.dht11, attempts=1, retry_interval=0)
        self.driver = Dht11Driver(None, self.config)
        patcher = patch('experiments.environment.jetson.dht11.subprocess.run')
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def valid(self):
        return CompletedProcess([], 0, '28.5 60.0\n', '')

    def test_default_new_project_helper_is_used_and_measurement_is_parsed(self):
        self.run.return_value = self.valid()
        self.assertEqual(self.driver.read(), {'temp': 28.5, 'humidity': 60.0})
        expected = PROJECT_ROOT / 'experiments/environment/jetson/native/dht11_gpiod'
        self.assertTrue(expected.is_file())
        self.assertEqual(self.run.call_args.args[0], (str(expected), '/dev/gpiochip1', '151'))
        self.assertEqual(self.run.call_args.kwargs['timeout'], self.config.native_timeout_seconds)

    def test_checksum_failure_cannot_publish_even_valid_looking_stdout(self):
        self.run.return_value = CompletedProcess([], 3, '28.5 60.0\n', 'checksum error')
        with self.assertRaisesRegex(RuntimeError, 'checksum error'):
            self.driver.read()
        self.assertIsNone(self.driver._last_value)

    def test_timeout_is_retried_and_next_valid_frame_is_accepted(self):
        driver = Dht11Driver(None, replace(self.config, attempts=2))
        self.run.side_effect = [TimeoutExpired('helper', .5), self.valid()]
        self.assertEqual(driver.read(), {'temp': 28.5, 'humidity': 60.0})
        self.assertEqual(self.run.call_count, 2)

    def test_retry_respects_normal_sampling_interval_after_failed_transaction(self):
        clock = {'now': 100.0}
        driver = Dht11Driver(None, replace(self.config, attempts=3, interval=2.0, retry_interval=1.1))
        starts = []
        def sample():
            starts.append(clock['now'])
            clock['now'] += .05
            if len(starts) < 3:
                raise RuntimeError('incomplete frame')
            return {'temp': 26.0, 'humidity': 54.0}
        with patch('experiments.environment.jetson.dht11.time.monotonic', side_effect=lambda: clock['now']), \
                patch('experiments.environment.jetson.dht11.time.sleep', side_effect=lambda seconds: clock.update(now=clock['now'] + seconds)), \
                patch.object(driver, '_read_once', side_effect=sample):
            self.assertEqual(driver.read(), {'temp': 26.0, 'humidity': 54.0})
        self.assertEqual(starts, [100.0, 102.0, 104.0])

    def test_incomplete_or_extra_native_output_is_rejected(self):
        for output in ('28.5', '', '28.5 60.0 diagnostic', 'bad 60.0'):
            with self.subTest(output=output):
                self.run.return_value = CompletedProcess([], 0, output, '')
                with self.assertRaises(RuntimeError):
                    Dht11Driver(None, self.config).read()

    def test_nonfinite_and_out_of_range_readings_are_rejected(self):
        for output in ('nan 60', 'inf 60', '28 nan', '28 101', '61 60', '-1 60'):
            with self.subTest(output=output):
                self.run.return_value = CompletedProcess([], 0, output, '')
                with self.assertRaisesRegex(RuntimeError, '数值超出'):
                    Dht11Driver(None, self.config).read()

    def test_cancellation_stops_retry_without_starting_another_helper(self):
        cancelled = threading.Event()
        cancelled.set()
        driver = Dht11Driver(None, replace(self.config, attempts=2), cancelled)
        self.run.return_value = CompletedProcess([], 2, '', 'data bit 38 did not fall')
        with self.assertRaisesRegex(RuntimeError, '采样已取消'):
            driver.read()
        self.assertEqual(self.run.call_count, 1)

    def test_cached_reading_does_not_trigger_a_second_measurement_too_soon(self):
        self.run.return_value = self.valid()
        first = self.driver.read()
        self.assertEqual(self.driver.read(), first)
        self.run.assert_called_once()

    def test_failed_background_read_then_button_read_waits_and_recovers(self):
        clock = {'now': 100.0}
        attempts = []

        def sample():
            attempts.append(clock['now'])
            if len(attempts) == 1:
                raise RuntimeError('checksum error')
            return {'temp': 28.5, 'humidity': 60.0}

        def wait(seconds):
            clock['now'] += seconds

        with patch('experiments.environment.jetson.dht11.time.monotonic',
                   side_effect=lambda: clock['now']), \
                patch('experiments.environment.jetson.dht11.time.sleep', side_effect=wait) as sleep, \
                patch.object(self.driver, '_read_once', side_effect=sample):
            with self.assertRaisesRegex(RuntimeError, 'checksum error'):
                self.driver.read()
            self.assertEqual(self.driver.read(), {'temp': 28.5, 'humidity': 60.0})
        sleep.assert_called_once_with(self.config.interval)
        self.assertEqual(attempts, [100.0, 100.0 + self.config.interval])

    def test_waiting_after_failure_can_be_cancelled_without_reading_gpio(self):
        cancelled = threading.Event()
        cancelled.set()
        driver = Dht11Driver(None, self.config, cancelled)
        driver._last_attempt = 100.0
        with patch('experiments.environment.jetson.dht11.time.monotonic', return_value=100.5):
            with self.assertRaisesRegex(RuntimeError, '采样已取消'):
                driver.read()
        self.run.assert_not_called()

    def test_real_failure_after_cooldown_is_not_hidden_by_old_valid_values(self):
        clock = {'now': 100.0}
        self.run.side_effect = [self.valid(),
            CompletedProcess([], 3, '', 'checksum error'),
            CompletedProcess([], 2, '', 'no sensor response')]
        with patch('experiments.environment.jetson.dht11.time.monotonic',
                   side_effect=lambda: clock['now']), \
                patch('experiments.environment.jetson.dht11.time.sleep',
                      side_effect=lambda seconds: clock.update(now=clock['now'] + seconds)):
            self.driver.read()
            clock['now'] += self.config.interval
            with self.assertRaisesRegex(RuntimeError, 'checksum error'):
                self.driver.read()
            self.assertIsNone(self.driver._last_value)
            with self.assertRaisesRegex(RuntimeError, 'no sensor response'):
                self.driver.read()
        self.assertEqual(self.run.call_count, 3)


if __name__ == '__main__':
    unittest.main()
