"""正式入口的生命周期与串口屏业务验证，不访问实际设备。"""
import unittest

from application import NingBoApplication


class DeviceFixture:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.fail_start = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("GPIO initialization failed")
        self.started = True

    def stop(self): self.stopped = True
    def emergency_stop(self): return {"ok": True}
    def snapshot(self):
        return {"sensors": {"dht11": {"temp": 25.36, "humidity": 63.2}, "bh1750": {"lux": 428}}}


class ScreenFixture:
    def __init__(self):
        self.events = []
        self.messages = []
        self.started = False
        self.stopped = False

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def read_screen_events(self):
        events, self.events = self.events, []
        return events
    def handle_message(self, message):
        self.messages.append(message)
        return [message]


class ApplicationTests(unittest.TestCase):
    def test_start_waits_for_button_then_stop_clears_values(self):
        devices, screen = DeviceFixture(), ScreenFixture()
        with NingBoApplication(devices=devices, screen=screen) as app:
            app.process_once()
            self.assertEqual(screen.messages, [])
            screen.events = [{"event": "environment.start"}]
            app.process_once()
            self.assertEqual(screen.messages[0], {"scene": "environment", "temperature": 25.36, "humidity": 63.2, "light": 43})
            screen.events = [{"event": "environment.stop"}]
            app.process_once()
            self.assertEqual(screen.messages[-1], {"scene": "environment", "temperature": 0, "humidity": 0, "light": 0})
            count = len(screen.messages)
            app.process_once()
            self.assertEqual(len(screen.messages), count)
        self.assertTrue(devices.stopped)
        self.assertTrue(screen.stopped)

    def test_device_start_failure_releases_screen(self):
        devices, screen = DeviceFixture(), ScreenFixture()
        devices.fail_start = True
        app = NingBoApplication(devices=devices, screen=screen)
        with self.assertRaisesRegex(RuntimeError, "GPIO initialization failed"):
            app.start()
        self.assertTrue(screen.stopped)
        self.assertFalse(app._started)

    def test_stop_request_exits_run_and_releases_devices(self):
        devices, screen = DeviceFixture(), ScreenFixture()
        with NingBoApplication(devices=devices, screen=screen) as app:
            app.request_stop()
            app.run()
        self.assertTrue(devices.stopped)
        self.assertTrue(screen.stopped)

    def test_main_stop_signals_use_normal_cleanup(self):
        import main
        import signal
        from unittest.mock import patch
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                class App:
                    def __init__(self, config):
                        self.requested = False
                        self.closed = False
                    def __enter__(self): return self
                    def __exit__(self, *args): self.closed = True
                    def request_stop(self): self.requested = True
                    def run(self): signal.getsignal(signum)(signum, None)
                previous = signal.getsignal(signum)
                instance = App(None)
                with patch.object(main, 'NingBoApplication', return_value=instance), patch.object(main.logging, 'basicConfig'):
                    self.assertEqual(main.main([]), 0)
                self.assertTrue(instance.requested)
                self.assertTrue(instance.closed)
                self.assertEqual(signal.getsignal(signum), previous)


if __name__ == "__main__":
    unittest.main()
