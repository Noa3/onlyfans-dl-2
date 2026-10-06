"""Safety boundaries for diagnostic parsing, using synthetic data only."""
import unittest

import requests

from ofdl.common import Cancelled, Control
from ofdl.diagnostics import MAX_ERROR_BYTES, details_from_json, inspect_error_response


class DiagnosticTests(unittest.TestCase):
    def test_unknown_and_secret_shaped_codes_are_discarded(self):
        for code in [True, -1, 10000, 'session-secret', {'token':'session-secret'}]:
            with self.subTest(code=code):
                details = details_from_json({'error':{'code':code,'message':'session-secret'}})
                self.assertIsNone(details.code)
                self.assertEqual(details.category, 'unknown')
                self.assertNotIn('session-secret', details.summary())

    def test_error_body_limit_stops_reading_before_stream_end(self):
        class Stream:
            headers = {'Content-Type':'application/json'}
            calls = 0
            def iter_content(self, size):
                for _ in range(100):
                    self.calls += 1
                    yield b'x' * size
        response = Stream()
        result = inspect_error_response(response, Control())
        self.assertEqual(result.body_state, 'oversized')
        self.assertLessEqual(response.calls*4096, MAX_ERROR_BYTES+4096)

    def test_error_read_connection_failure_does_not_echo_exception(self):
        class Stream:
            headers = {'Content-Type':'application/json'}
            def iter_content(self, size):
                raise requests.ConnectionError('session-secret in request URL')
        result = inspect_error_response(Stream(), Control())
        self.assertEqual(result.body_state, 'unreadable')
        self.assertNotIn('session-secret', result.summary())

    def test_error_body_cancellation_is_not_swallowed(self):
        control = Control()
        class Stream:
            headers = {}
            def iter_content(self, size):
                control.stop()
                yield b'{}'
        with self.assertRaises(Cancelled):
            inspect_error_response(Stream(), control)

    def test_json_shape_and_empty_error_messages_do_not_create_diagnosis(self):
        for data in [None, [], True, 'secret', {'message':'Invalid sign'}, {'error':{}}, {'error':'secret'}]:
            with self.subTest(kind=type(data).__name__):
                result = details_from_json(data)
                self.assertEqual(result.category, 'unknown')
                self.assertIsNone(result.code)
                self.assertNotIn('secret', result.summary())
