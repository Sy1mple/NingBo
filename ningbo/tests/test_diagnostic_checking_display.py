"""A fast completed job must still show its new checking state on the HMI."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from system.diagnostics.bridge import DiagnosticsScreenBridge


class CheckingDisplayTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.report = {'revision': 3, 'results': {'dht11': {'status': 'normal'}},
                       'busy': False, 'progress': 100, 'overall': 'normal'}
        self.devices = SimpleNamespace(request_diagnostics=Mock(return_value={'accepted': True, 'note': 'started'}),
                                       diagnostic_snapshot=lambda: self.report)
        self.screen = SimpleNamespace(handle_message=Mock(return_value=[]))
        self.bridge = DiagnosticsScreenBridge(self.screen, self.devices, clock=lambda: self.now)

    def test_fast_job_shows_checking_then_result_without_a_revision_change(self):
        self.bridge.handle_event('diagnostics.sensors')
        self.bridge.tick()
        self.assertEqual(self.screen.handle_message.call_args.args[0]['statuses'], {'dht11': 'checking'})
        self.now += .49
        self.bridge.tick()
        self.assertEqual(self.screen.handle_message.call_count, 1)
        self.now += .02
        self.bridge.tick()
        self.assertEqual(self.screen.handle_message.call_args.args[0]['statuses'], {'dht11': 'normal'})
        self.assertEqual(self.devices.request_diagnostics.call_count, 1)

    def test_every_new_accepted_click_resets_previous_normal_to_checking(self):
        for _ in range(2):
            self.bridge.handle_event('diagnostics.sensors')
            self.bridge.tick()
            self.assertEqual(self.screen.handle_message.call_args.args[0]['statuses']['dht11'], 'checking')
            self.now += 1
            self.bridge.tick()
            self.assertEqual(self.screen.handle_message.call_args.args[0]['statuses']['dht11'], 'normal')

    def test_busy_click_does_not_reset_active_job_or_extend_visual_delay(self):
        self.bridge.handle_event('diagnostics.sensors')
        deadline = self.bridge._checking_until
        self.now += .4
        self.devices.request_diagnostics.return_value = {'accepted': False, 'note': 'busy'}
        self.assertFalse(self.bridge.handle_event('diagnostics.sensors')['handled'])
        self.assertEqual(self.bridge._checking_until, deadline)

    def test_slow_job_remains_checking_until_real_result_arrives(self):
        self.report.update(busy=True, overall='checking', progress=0)
        self.report['results']['dht11']['status'] = 'checking'
        self.bridge.handle_event('diagnostics.sensors')
        self.bridge.tick()
        self.now += 1
        self.bridge.tick()
        self.assertEqual(self.screen.handle_message.call_args.args[0]['statuses']['dht11'], 'checking')
        self.report['revision'] += 1
        self.report['results']['dht11']['status'] = 'error'
        self.report.update(busy=False, progress=100, overall='error')
        self.bridge.tick()
        self.assertEqual(self.screen.handle_message.call_args.args[0]['statuses']['dht11'], 'error')


if __name__ == '__main__': unittest.main()
