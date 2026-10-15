import subprocess
import unittest
from dataclasses import replace
from unittest.mock import patch

from JETSON.Sensor.Config import DEFAULT_CONFIG
from JETSON.Sensor.sensors.dht11 import Dht11Driver


class Dht11NativeTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(
            DEFAULT_CONFIG.dht11,
            native_helper="JETSON/Sensor/native/dht11_gpiod.c",
            attempts=1,
        )
        self.driver = Dht11Driver(None, self.config)

    @patch("JETSON.Sensor.sensors.dht11.subprocess.run")
    def test_parses_native_measurement(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "27.2 43.0\n", "")
        self.assertEqual(self.driver.read(), {"temp": 27.2, "humidity": 43.0})
        args = run.call_args.args[0]
        self.assertEqual(args[-2:], ("/dev/gpiochip1", "151"))

    @patch("JETSON.Sensor.sensors.dht11.subprocess.run")
    def test_reports_native_checksum_failure(self, run):
        run.return_value = subprocess.CompletedProcess([], 3, "", "checksum error")
        with self.assertRaisesRegex(RuntimeError, "checksum error"):
            self.driver.read()


if __name__ == "__main__":
    unittest.main()
