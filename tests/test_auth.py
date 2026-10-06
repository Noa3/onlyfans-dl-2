import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from ofdl.auth import Credentials, parse_session, SessionError
    from ofdl.settings import save_preferences, load_preferences
except ImportError:
    Credentials = parse_session = SessionError = save_preferences = load_preferences = None

RAW = 'User-Agent: TestBrowser/1.0\nx-bc: abc123\nCookie: auth_id=123; sess=secret-token; auth_uid_123=other-token\n'


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(parse_session, 'Session importer is not implemented')

    def test_raw_headers_extract_all_fields(self):
        auth = parse_session(RAW)
        self.assertEqual((auth.user_id, auth.user_agent, auth.x_bc, auth.sess_cookie),
                         ('123', 'TestBrowser/1.0', 'abc123', 'secret-token'))
        self.assertIn('auth_uid_123=other-token', auth.cookie_header())

    def test_curl_cookie_option(self):
        auth = parse_session("curl 'https://onlyfans.com/api2/v2/users/me' -H 'user-agent: TestBrowser/1.0' -H 'x-bc: abc123' -b 'auth_id=123; sess=secret-token'")
        self.assertEqual(auth.user_id, '123')
        self.assertEqual(auth.sess_cookie, 'secret-token')

    def test_curl_windows_continuations(self):
        auth = parse_session('curl "https://onlyfans.com/api2/v2/users/me" ^\n -H "user-agent: TestBrowser/1.0" ^\n -H "x-bc: abc123" ^\n -b "auth_id=123; sess=secret-token"')
        self.assertEqual(auth.x_bc, 'abc123')

    def test_curl_user_agent_flag(self):
        auth = parse_session("curl https://onlyfans.com/api2/v2/users/me --user-agent 'TestBrowser/1.0' --header='x-bc: abc123' --cookie='auth_id=123; sess=secret-token'")
        self.assertEqual(auth.user_agent, 'TestBrowser/1.0')

    def test_alternating_devtools_headers(self):
        auth = parse_session('user-agent\nTestBrowser/1.0\nx-bc\nabc123\ncookie\nauth_id=123; sess=secret-token')
        self.assertEqual(auth.user_id, '123')

    def test_uppercase_configuration_json(self):
        auth = parse_session(json.dumps({'USER_ID': '123', 'USER_AGENT': 'UA', 'X_BC': 'bc', 'SESS_COOKIE': 'secret-token'}))
        self.assertEqual(auth.user_id, '123')

    def test_har_selects_onlyfans_request_not_other_domain(self):
        headers = [{'name': k, 'value': v} for k, v in {'user-agent':'UA','x-bc':'bc','cookie':'auth_id=123; sess=good'}.items()]
        har = {'log': {'entries': [{'request': {'url':'https://onlyfans.com/api2/v2/users/me', 'headers':headers}},
                                 {'request': {'url':'https://evil.example/api2/v2/users/me', 'headers':headers}}]}}
        self.assertEqual(parse_session(json.dumps(har)).sess_cookie, 'good')

    def test_har_sanitized_cookie_is_actionable(self):
        har = {'log': {'entries': [{'request': {'url':'https://onlyfans.com/api2/v2/users/me', 'headers':[{'name':'x-bc','value':'bc'}]}}]}}
        with self.assertRaisesRegex(SessionError, '(?i)(cookie|sanitized|complete)'):
            parse_session(json.dumps(har))

    def test_conflicting_account_ids_rejected(self):
        with self.assertRaisesRegex(SessionError, '(?i)(match|different|conflict)'):
            parse_session(RAW + 'user-id: 456\n')

    def test_import_does_not_execute_shell_code(self):
        with TemporaryDirectory() as folder:
            marker = Path(folder) / 'should-not-exist'
            text = f"curl https://onlyfans.com/api2/v2/users/me -H 'user-agent: UA' -H 'x-bc: bc' -b 'auth_id=123; sess=token' ; touch {marker}"
            parse_session(text)
            self.assertFalse(marker.exists())

    def test_wrong_domain_curl_rejected(self):
        with self.assertRaises(SessionError):
            parse_session("curl https://onlyfans.com.evil.example/api2/v2/me -H 'user-agent: UA' -H 'x-bc: bc' -b 'auth_id=123; sess=token'")

    def test_missing_fields_dont_echo_cookie(self):
        with self.assertRaises(SessionError) as ctx:
            parse_session('Cookie: sess=top-secret-token')
        self.assertNotIn('top-secret-token', str(ctx.exception))
        self.assertIn('USER_ID', str(ctx.exception))

    def test_header_injection_rejected(self):
        with self.assertRaises(SessionError):
            Credentials('123', 'UA\r\nBad: yes', 'bc', 'token').validate()

    def test_session_repr_is_redacted(self):
        auth = parse_session(RAW)
        self.assertNotIn('secret-token', repr(auth))
        self.assertNotIn('abc123', repr(auth))

    def test_preferences_never_save_credentials(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            save_preferences({'output_dir':folder, 'user_id':'123', 'sess_cookie':'secret-token', 'x_bc':'abc123', 'user_agent':'UA', 'profiles':'creator'}, path)
            saved = path.read_text()
            self.assertNotIn('secret-token', saved)
            self.assertNotIn('abc123', saved)
            self.assertNotIn('user_id', saved)
            self.assertEqual(load_preferences(path)['profiles'], 'creator')


class CredentialStoreTests(unittest.TestCase):
    def backend(self,module='keyring.backends.Windows'):
        class Store:
            priority = 1
            value = None
            writes = 0
            def set_password(self,service,account,value):
                self.value = value
                self.writes += 1
            def get_password(self,service,account):
                return self.value
            def delete_password(self,service,account):
                self.value = None
        Store.__module__ = module
        return Store()

    def test_explicit_save_load_forget_round_trip(self):
        from ofdl.auth import save_session,load_session,forget_session
        store = self.backend()
        auth = parse_session(RAW)
        with patch.dict('sys.modules',{'keyring':SimpleNamespace(get_keyring=lambda:store)}):
            self.assertEqual(store.writes,0)
            save_session(auth)
            self.assertEqual(store.writes,1)
            self.assertEqual(load_session(),auth)
            forget_session()
            self.assertIsNone(store.value)

    def test_plaintext_backend_is_refused_before_write(self):
        from ofdl.auth import save_session
        store = self.backend('keyrings.alt.file')
        with patch.dict('sys.modules',{'keyring':SimpleNamespace(get_keyring=lambda:store)}):
            with self.assertRaisesRegex(SessionError,'Plaintext fallback is disabled'):
                save_session(parse_session(RAW))
        self.assertEqual(store.writes,0)
        self.assertIsNone(store.value)

    def test_missing_os_entry_has_actionable_message(self):
        from ofdl.auth import load_session
        store = self.backend()
        with patch.dict('sys.modules',{'keyring':SimpleNamespace(get_keyring=lambda:store)}):
            with self.assertRaisesRegex(SessionError,'no saved session'):
                load_session()

    def test_os_backend_error_does_not_reveal_secret(self):
        from ofdl.auth import save_session
        store = self.backend()
        def fail(*args):
            raise RuntimeError('private-cookie-value')
        store.set_password = fail
        with patch.dict('sys.modules',{'keyring':SimpleNamespace(get_keyring=lambda:store)}):
            with self.assertRaises(SessionError) as error:
                save_session(parse_session(RAW))
        self.assertNotIn('private-cookie-value',str(error.exception))


if __name__ == '__main__':
    unittest.main()
