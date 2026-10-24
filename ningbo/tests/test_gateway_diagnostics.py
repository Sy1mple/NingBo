"""Single-actuator calibration requests and read-only raw diagnostic reports."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

from gateway.server import GatewayHandler


class GatewayDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.devices = SimpleNamespace(request_diagnostics=Mock(), diagnostic_snapshot=Mock())
        handler = type('TestHandler', (GatewayHandler,), {
            'devices': cls.devices,
            'settings': SimpleNamespace(require_auth=False, token=''),
            'log_message': lambda *args: None,
        })
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:%d/api/diagnostics' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.devices.request_diagnostics.reset_mock()
        self.devices.diagnostic_snapshot.reset_mock()
        self.devices.request_diagnostics.return_value = {'accepted': True, 'busy': True}

    def post(self, data):
        return urllib.request.urlopen(urllib.request.Request(self.url,
            data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'}, method='POST'), timeout=2)

    def test_read_returns_raw_samples_without_starting_hardware(self):
        report = {'busy': False, 'results': {'fan': {'status': 'error', 'samples_volts': [.042, .043]}}}
        self.devices.diagnostic_snapshot.return_value = report
        with urllib.request.urlopen(self.url, timeout=2) as response:
            self.assertEqual(json.load(response)['report'], report)
        self.devices.request_diagnostics.assert_not_called()

    def test_start_only_one_explicit_valid_target(self):
        with self.post({'target': 'fan'}) as response:
            self.assertTrue(json.load(response)['ok'])
        self.devices.request_diagnostics.assert_called_once_with(target='fan')

    def test_invalid_or_missing_target_does_not_start_any_job(self):
        for data in ({}, {'target': '../lock'}, {'target': ['fan']}, {'target': 'all'}):
            with self.subTest(data=data), self.assertRaises(urllib.error.HTTPError) as caught:
                self.post(data)
            self.assertEqual(caught.exception.code, 400)
        self.devices.request_diagnostics.assert_not_called()

    def test_busy_job_is_reported_without_retry(self):
        self.devices.request_diagnostics.return_value = {'accepted': False, 'busy': True}
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post({'target': 'servo'})
        self.assertEqual(caught.exception.code, 409)
        self.devices.request_diagnostics.assert_called_once_with(target='servo')
