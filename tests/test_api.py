import hashlib
import io
import json
import threading
import unittest
from unittest.mock import patch

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict
from ofdl.auth import Credentials

try:
    from ofdl.api import Rules, ApiClient, ApiError, load_rules, retry_delay
    from ofdl.common import AppError, Cancelled, Control
except ImportError:
    Rules = ApiClient = load_rules = retry_delay = AppError = Cancelled = Control = None

RULE_DATA = {'static_param':'fixture', 'format':'12:{}:{:x}:34', 'checksum_indexes':[0,3,39],
             'checksum_constant':-25, 'app_token':'public-token', 'remove_headers':[]}
AUTH = Credentials('123', 'TestBrowser/1.0', 'bc-secret', 'session-secret')


class Adapter(BaseAdapter):
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.options = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        self.options.append(kwargs)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        status, data, headers = reply
        response = requests.Response()
        response.status_code = status
        response.headers = CaseInsensitiveDict(headers)
        response.url = request.url
        response.request = request
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
            response.headers.setdefault('Content-Type', 'application/json')
        response.raw = io.BytesIO(data)
        return response

    def close(self):
        pass


def session_for(replies):
    session = requests.Session()
    adapter = Adapter(replies)
    session.mount('https://', adapter)
    return session, adapter


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ApiClient, 'API layer is not implemented')

    def client(self, replies, rule_data=None, control=None):
        session, adapter = session_for(replies)
        client = ApiClient(AUTH, Rules.from_dict(rule_data or RULE_DATA), control or Control(),
                           session=session, interval=0, retries=0)
        self.addCleanup(client.close)
        return client, adapter

    def test_signature_uses_exact_prepared_query_and_milliseconds(self):
        client, adapter = self.client([(200, {'id':123}, {})])
        with patch('ofdl.api.time.time', return_value=1000.125):
            client.request('/users/me', {'search':'a & b', 'limit':50})
        request = adapter.requests[0]
        self.assertEqual(request.path_url, '/api2/v2/users/me?search=a+%26+b&limit=50')
        self.assertEqual(request.headers['time'], '1000125')
        digest = hashlib.sha1(('fixture\n1000125\n' + request.path_url + '\n123').encode()).hexdigest()
        expected = RULE_DATA['format'].format(digest, abs(sum(ord(digest[i]) for i in [0,3,39])-25))
        self.assertEqual(request.headers['sign'], expected)
        self.assertIn('auth_id=123', request.headers['Cookie'])
        self.assertNotIn('auh_id=', request.headers['Cookie'])
        self.assertTrue(adapter.options[0]['verify'])

    def test_rules_cannot_remove_the_authenticated_signing_identity(self):
        rules = dict(RULE_DATA, remove_headers=['user-id'])
        client, adapter = self.client([(200,{}, {})], rules)
        client.request('/users/me')
        self.assertEqual(adapter.requests[0].headers.get('user-id'), AUTH.user_id)
        self.assertIn('sign', adapter.requests[0].headers)

    def test_header_names_preserve_underscores(self):
        rules = dict(RULE_DATA, remove_headers=['User_Id', 'X_Optional'])
        parsed = Rules.from_dict(rules)
        self.assertEqual(parsed.remove_headers, ('user_id', 'x_optional'))

    def test_optional_headers_can_be_removed_without_changing_signed_identity(self):
        client, adapter = self.client([(200,{}, {})], dict(RULE_DATA, remove_headers=['Accept-Encoding']))
        client.request('/users/me')
        self.assertNotIn('Accept-Encoding', adapter.requests[0].headers)
        self.assertEqual(adapter.requests[0].headers.get('user-id'), AUTH.user_id)

    def test_prepared_request_passes_an_independent_signed_identity_contract(self):
        # A fake endpoint checks the emitted request, not a predetermined HTTP 200.
        # This models the documented client contract; it is NOT a live service test.
        class ContractAdapter(Adapter):
            def send(self, request, **kwargs):
                fields = request.headers
                identity = fields.get('user-id', '0')
                digest = hashlib.sha1(('fixture\n' + fields['time'] + '\n' + request.path_url + '\n' + identity).encode()).hexdigest()
                checksum = abs(sum(ord(digest[i]) for i in [0,3,39]) - 25)
                valid = identity == '123' and fields.get('sign') == f'12:{digest}:{checksum:x}:34'
                self.replies = iter([(200, {'id':123}, {})] if valid else [(400, {'error':{'code':101,'message':'Invalid sign'}}, {})])
                return super().send(request, **kwargs)
        session = requests.Session()
        adapter = ContractAdapter([])
        session.mount('https://', adapter)
        with ApiClient(AUTH, Rules.from_dict(dict(RULE_DATA, remove_headers=['user-id'])),
                       Control(), session=session, interval=0, retries=0) as client:
            self.assertEqual(client.user()['id'], 123)
        self.assertEqual(len(adapter.requests), 1)

    def test_malformed_rule_indexes_rejected(self):
        for indexes in [[40],[-1],[True],[],['3']]:
            with self.subTest(indexes=indexes), self.assertRaises(AppError):
                Rules.from_dict(dict(RULE_DATA, checksum_indexes=indexes))

    def test_unsafe_format_fields_rejected(self):
        with self.assertRaises(AppError):
            Rules.from_dict(dict(RULE_DATA, format='{0.__class__}:{}:{:x}:test'))

    def test_legacy_prefix_suffix_rules_supported(self):
        rules = dict(RULE_DATA)
        rules.pop('format')
        rules.update(prefix='12',suffix='34')
        self.assertEqual(Rules.from_dict(rules).format, '12:{}:{:x}:34')

    def test_wrapped_short_page_has_more_continues(self):
        client, adapter = self.client([(200,{'list':[{'id':1}], 'hasMore':True},{}),
                                      (200,{'list':[{'id':2}], 'hasMore':False},{})])
        rows = list(client.paginate('/subscriptions/subscribes','subscriptions'))
        self.assertEqual([r['id'] for r in rows], [1,2])
        self.assertIn('offset=1', adapter.requests[1].url)

    def test_full_final_page_has_more_false_stops(self):
        client, adapter = self.client([(200,{'list':[{'id':i} for i in range(50)], 'hasMore':False},{})])
        self.assertEqual(len(list(client.paginate('/subscriptions/subscribes','subscriptions'))), 50)
        self.assertEqual(len(adapter.requests),1)

    def test_plain_list_offset_pagination(self):
        client, adapter = self.client([(200,[{'id':i} for i in range(50)],{}),(200,[{'id':50}],{})])
        self.assertEqual(len(list(client.paginate('/posts/paid/all','purchased'))),51)
        self.assertIn('offset=50',adapter.requests[1].url)

    def test_messages_use_message_id_cursor(self):
        client, adapter = self.client([(200,{'list':[{'id':8}], 'hasMore':True},{}),
                                      (200,{'list':[{'id':7}], 'hasMore':False},{})])
        self.assertEqual([row['id'] for row in client.paginate('/chats/3/messages','messages')],[8,7])
        self.assertIn('id=8',adapter.requests[1].url)
        self.assertNotIn('afterPublishTime',adapter.requests[1].url)

    def test_posts_use_precise_time_cursor(self):
        client, adapter = self.client([(200,{'list':[{'id':1,'postedAtPrecise':'1600000000.123456'}], 'hasMore':True},{}),
                                      (200,{'list':[{'id':2}], 'hasMore':False},{})])
        self.assertEqual(len(list(client.paginate('/users/1/posts','posts'))),2)
        self.assertIn('afterPublishTime=1600000000.123456',adapter.requests[1].url)

    def test_failed_later_page_never_reuses_previous_page(self):
        client, _ = self.client([(200,{'list':[{'id':1}], 'hasMore':True},{}),(500,{}, {})])
        iterator = client.paginate('/subscriptions/subscribes','subscriptions')
        self.assertEqual(next(iterator)['id'],1)
        with self.assertRaises(AppError):
            next(iterator)

    def test_repeated_page_fails_instead_of_infinite_loop(self):
        page = {'list':[{'id':1}], 'hasMore':True}
        client, _ = self.client([(200,page,{}),(200,page,{})])
        with self.assertRaisesRegex(AppError,'(?i)(repeat|progress|pagination)'):
            list(client.paginate('/posts/paid/all','purchased'))

    def test_overlapping_pages_deduplicate(self):
        client, _ = self.client([(200,{'list':[{'id':1},{'id':2}], 'hasMore':True},{}),
                                 (200,{'list':[{'id':2},{'id':3}], 'hasMore':False},{})])
        self.assertEqual([r['id'] for r in client.paginate('/posts/paid/all','purchased')],[1,2,3])

    def test_invalid_envelope_does_not_look_like_empty_success(self):
        client, _ = self.client([(200, {'unexpected':'shape'}, {})])
        with self.assertRaises(AppError):
            list(client.paginate('/posts/paid/all','purchased'))

    def test_error_json_does_not_echo_sensitive_body(self):
        client, _ = self.client([(401, {'error':'session-secret'}, {})])
        with self.assertRaises(AppError) as ctx:
            client.request('/users/me')
        self.assertNotIn('session-secret', str(ctx.exception))
        self.assertIn('401',str(ctx.exception))

    def test_http400_reports_known_error_without_raw_response(self):
        client, adapter = self.client([(400, {'error':{'code':101,'message':'Invalid sign'},
                                               'private':'session-secret'}, {})])
        client.retries = 3
        with self.assertRaises(ApiError) as ctx:
            client.user()
        text = str(ctx.exception)
        self.assertIn('stage=api', text)
        self.assertIn('category=invalid-signature', text)
        self.assertIn('code=101', text)
        self.assertIn('response=json', text)
        self.assertIn('user-id-header=present', text)
        self.assertIn('time-unit=milliseconds', text)
        self.assertRegex(text, r'rules=[0-9a-f]{12}')
        for secret in ['session-secret','bc-secret','TestBrowser/1.0']:
            self.assertNotIn(secret, text)
        self.assertEqual(len(adapter.requests), 1, 'Do not blindly retry HTTP 400')

    def test_unrecognized_server_text_is_never_echoed_or_overclassified(self):
        data = {'error':{'code':'secret-code','message':'Invalid sign session-secret'},
                'cookie':'session-secret'}
        client, _ = self.client([(400, data, {})])
        with self.assertRaises(ApiError) as ctx:
            client.user()
        text = str(ctx.exception)
        self.assertIn('category=unknown', text)
        self.assertIn('code=unavailable', text)
        self.assertNotIn('session-secret', text)
        self.assertNotIn('secret-code', text)
        self.assertNotIn('category=invalid-signature', text)

    def test_refresh_page_response_is_not_assumed_to_prove_invalid_signature(self):
        client, _ = self.client([(400, {'error':{'code':0,'message':'Please refresh the page'}}, {})])
        with self.assertRaises(ApiError) as ctx:
            client.user()
        self.assertIn('category=refresh-required', str(ctx.exception))
        self.assertNotIn('category=invalid-signature', str(ctx.exception))

    def test_html_error_has_safe_type_and_no_body_or_headers(self):
        client, _ = self.client([(403, b'<html>session-secret</html>',
                                 {'Content-Type':'text/html','Set-Cookie':'sess=session-secret'})])
        with self.assertRaises(ApiError) as ctx:
            client.user()
        self.assertIn('response=html', str(ctx.exception))
        self.assertNotIn('session-secret', str(ctx.exception))

    def test_malformed_error_json_preserves_http_status(self):
        client, _ = self.client([(400, b'{session-secret', {'Content-Type':'application/json'})])
        with self.assertRaises(ApiError) as ctx:
            client.user()
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn('body=unreadable', str(ctx.exception))
        self.assertNotIn('session-secret', str(ctx.exception))

    def test_oversized_error_body_is_bounded_without_losing_status(self):
        client, _ = self.client([(400, {'error':{'message':'x'*20000}}, {})])
        with self.assertRaises(ApiError) as ctx:
            client.user()
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn('body=oversized', str(ctx.exception))

    def test_http200_error_object_remains_a_rejection(self):
        client, _ = self.client([(200, {'error':{'code':101,'message':'Invalid sign'}}, {})])
        with self.assertRaises(ApiError) as ctx:
            client.user()
        self.assertIn('error object', str(ctx.exception))
        self.assertIn('category=invalid-signature', str(ctx.exception))
        self.assertEqual(ctx.exception.status, 200)

    def test_clock_diagnostic_uses_server_date_without_modifying_clock(self):
        for now, expected in [(1000, 'within-five-minutes'), (2000, 'local-ahead'), (0, 'local-behind')]:
            with self.subTest(expected=expected):
                client, _ = self.client([(400, {}, {'Date':'Thu, 01 Jan 1970 00:16:40 GMT'})])
                with patch('ofdl.api.time.time', return_value=now), self.assertRaises(ApiError) as ctx:
                    client.user()
                self.assertIn('clock=' + expected, str(ctx.exception))

    def test_invalid_server_date_is_unknown_not_echoed(self):
        client, _ = self.client([(400, {}, {'Date':'session-secret'})])
        with self.assertRaises(ApiError) as ctx:
            client.user()
        self.assertIn('clock=unknown', str(ctx.exception))
        self.assertNotIn('session-secret', str(ctx.exception))

    def test_public_rules_fingerprint_changes_only_with_rule_content(self):
        first = Rules.from_dict(RULE_DATA)
        second = Rules.from_dict(dict(RULE_DATA, checksum_constant=99))
        self.assertRegex(getattr(first, 'fingerprint', ''), r'^[0-9a-f]{12}$')
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.fingerprint, Rules.from_dict(dict(reversed(list(RULE_DATA.items())))).fingerprint)

    def test_session_test_rejects_a_different_returned_account(self):
        client, _ = self.client([(200, {'id':99999,'name':'private-name'}, {})])
        with self.assertRaisesRegex(AppError, '(?i)account.*match') as ctx:
            client.user('me')
        self.assertNotIn('99999', str(ctx.exception))
        self.assertNotIn('private-name', str(ctx.exception))

    def test_other_profile_lookups_do_not_need_to_match_own_account(self):
        client, _ = self.client([(200, {'id':99999}, {})])
        self.assertEqual(client.user('creator')['id'], 99999)

    def test_api_redirect_not_followed(self):
        client, adapter = self.client([(302,b'',{'Location':'https://evil.example'})])
        with self.assertRaises(AppError):
            client.request('/users/me')
        self.assertEqual(len(adapter.requests),1)

    def test_endpoint_cannot_change_origin(self):
        client, adapter = self.client([])
        for endpoint in ['https://evil.example', '//evil.example','/../evil','/users/me?token=secret']:
            with self.subTest(endpoint=endpoint), self.assertRaises(AppError):
                client.request(endpoint)
        self.assertFalse(adapter.requests)

    def test_cancellation_stops_before_request(self):
        control = Control()
        control.stop()
        client, adapter = self.client([], control=control)
        with self.assertRaises(Cancelled):
            client.request('/users/me')
        self.assertFalse(adapter.requests)

    def test_rules_source_gets_no_session_headers(self):
        session, adapter = session_for([(200,RULE_DATA,{})])
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as folder:
            rules = load_rules('https://rules.example/rules.json', Control(), cache_dir=Path(folder), session=session)
        self.assertEqual(rules.app_token,'public-token')
        self.assertNotIn('Cookie',adapter.requests[0].headers)
        self.assertNotIn('x-bc',adapter.requests[0].headers)
        self.assertNotIn('session-secret',repr(adapter.requests[0].headers))

    def test_rules_source_requires_https(self):
        with self.assertRaises(AppError):
            load_rules('http://rules.example/rules.json',Control())

    def test_retry_after_numeric_and_date(self):
        self.assertEqual(retry_delay('12',0),12)
        self.assertEqual(retry_delay('Thu, 01 Jan 1970 00:00:10 GMT',0),10)
        self.assertIsNone(retry_delay('not a date',0))

    def test_cancel_interrupts_retry_delay(self):
        control = Control()
        timer = threading.Timer(.02,control.stop)
        timer.start()
        try:
            with self.assertRaises(Cancelled):
                control.wait(10)
        finally:
            timer.cancel()


if __name__ == '__main__':
    unittest.main()
