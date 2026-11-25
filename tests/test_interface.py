import json
import importlib.util
import os
import threading
import time
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
from ofdl.auth import Credentials
from ofdl.common import AppError, Cancelled, Control
from ofdl.settings import load_preferences
try:
    from ofdl.browser import credentials_from_response, capture_context
    from ofdl.ui import App
    from ofdl.cli import build_parser
except ImportError:
    credentials_from_response = capture_context = App = build_parser = None


PLAYWRIGHT_AVAILABLE = importlib.util.find_spec('playwright') is not None


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(credentials_from_response,'Browser/UI/CLI interfaces are not implemented')

    def response(self,url='https://onlyfans.com/api2/v2/users/me',status=200,method='GET',data=None,mime='application/json'):
        class Request:
            def all_headers(self):
                return {'user-agent':'UA','x-bc':'bc','cookie':'auth_id=123; sess=secret'}
        class Response:
            request = Request()
        result = Response()
        result.url = url
        result.status = status
        result.request.method = method
        result.headers = {'content-type':mime}
        def read_json():
            if isinstance(data, Exception):
                raise data
            return {'id':123} if data is None else data
        result.json = read_json
        result.request.response = lambda: result
        result.request.url = url
        return result

    def test_browser_capture_only_accepts_successful_platform_api_get(self):
        auth = credentials_from_response(self.response())
        self.assertEqual(auth.user_id,'123')
        for kwargs in [{'url':'https://evil.example/api2/v2/users/me'},{'status':401},{'method':'POST'}]:
            self.assertIsNone(credentials_from_response(self.response(**kwargs)))

    def test_browser_capture_requires_own_account_response(self):
        for data in [{'id':99999}, {'id':0}, {'error':{'message':'private'}}, {}, []]:
            with self.subTest(data=data):
                self.assertIsNone(credentials_from_response(self.response(data=data)))

    def test_browser_capture_rejects_public_api_success_and_html(self):
        for kwargs in [{'url':'https://onlyfans.com/api2/v2/init'},
                       {'url':'https://onlyfans.com/api2/v2/users/creator'},
                       {'mime':'text/html'}, {'data':ValueError('secret response body')}]:
            with self.subTest(kind=list(kwargs)):
                self.assertIsNone(credentials_from_response(self.response(**kwargs)))

    def test_browser_capture_waits_for_finished_account_request_and_detaches(self):
        response = self.response()
        class Page:
            def goto(self, *args, **kwargs):
                pass
            def is_closed(self):
                return False
            def wait_for_timeout(self, ms):
                callback = context.listeners.get('requestfinished')
                if callback:
                    callback(response.request)
        class Context:
            def __init__(self):
                self.listeners = {}
                self.pages = [Page()]
            def new_page(self):
                return self.pages[0]
            def on(self, event, callback):
                self.listeners[event] = callback
            def remove_listener(self, event, callback):
                self.listeners.pop(event)
        context = Context()
        logs = []
        auth = capture_context(context, Control(), timeout=.05, emit=lambda kind,text:logs.append(text))
        self.assertEqual(auth.user_id, '123')
        self.assertFalse(context.listeners)
        self.assertIn('own-account', ' '.join(logs))
        self.assertNotIn('secret', ' '.join(logs))

    @unittest.skipUnless(PLAYWRIGHT_AVAILABLE, 'Optional Playwright package is not installed')
    def test_browser_launch_errors_have_safe_stage_and_category(self):
        from playwright.sync_api import Error
        from ofdl.browser import capture_session
        for error, category in [("Executable doesn't exist at private-path",'missing-browser'),
                                ('Timeout 30000ms exceeded private-path','timeout'),
                                ('unexpected private-path','unknown')]:
            with self.subTest(category=category), patch('playwright.sync_api.sync_playwright') as factory:
                factory.return_value.__enter__.return_value.chromium.launch.side_effect = Error(error)
                with self.assertRaises(AppError) as ctx:
                    capture_session(Control())
                text = str(ctx.exception)
                self.assertIn('stage=browser-launch', text)
                self.assertIn('category=' + category, text)
                self.assertNotIn('private-path', text)

    @unittest.skipUnless(PLAYWRIGHT_AVAILABLE, 'Optional Playwright package is not installed')
    def test_browser_capture_error_is_distinct_from_launch(self):
        from playwright.sync_api import Error
        from ofdl.browser import capture_session
        with patch('playwright.sync_api.sync_playwright') as factory, patch('ofdl.browser.capture_context') as capture:
            capture.side_effect = Error('Target page, context or browser has been closed: private-path')
            with self.assertRaises(AppError) as ctx:
                capture_session(Control())
            self.assertIn('stage=browser-capture', str(ctx.exception))
            self.assertIn('category=browser-closed', str(ctx.exception))
            self.assertNotIn('private-path', str(ctx.exception))

    @unittest.skipUnless(PLAYWRIGHT_AVAILABLE, 'Optional Playwright package is not installed')
    def test_browser_cleanup_error_does_not_lose_captured_session(self):
        from playwright.sync_api import Error
        from ofdl.browser import capture_session
        auth = Credentials('123','UA','bc','secret')
        logs = []
        with patch('playwright.sync_api.sync_playwright') as factory, patch('ofdl.browser.capture_context', return_value=auth):
            browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
            browser.new_context.return_value.close.side_effect = Error('private cleanup details')
            browser.close.side_effect = Error('private cleanup details')
            self.assertEqual(capture_session(Control(), emit=lambda k,v:logs.append(v)), auth)
        self.assertNotIn('private cleanup details', ' '.join(logs))

    def test_cli_options_parse(self):
        args = build_parser().parse_args(['--cli','creator','--days','7','--scan-only','--session-stdin'])
        self.assertTrue(args.cli)
        self.assertTrue(args.scan_only)
        self.assertTrue(args.session_stdin)
        self.assertEqual(args.days,7)

    def test_cli_does_not_offer_secret_values_in_command_arguments(self):
        help_text = build_parser().format_help()
        self.assertNotIn('--sess-cookie',help_text)
        self.assertNotIn('--x-bc',help_text)


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt','A graphical display is required')
class UITests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(App,'UI not implemented')
        import tkinter as tk
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root,preferences_path=Path(self.temp.name)/'settings.json')
        self.root.update()
        self.addCleanup(self.root.destroy)

    def test_ui_builds_all_tabs_and_starts_idle(self):
        self.assertEqual(len(self.app.tabs.tabs()),7)
        self.assertEqual(self.app.status.get(),'Ready')
        self.assertIsNone(self.app.worker)
        self.assertTrue(hasattr(self.app,'duplicate_tree'))
        self.assertTrue(hasattr(self.app,'offer_tree'))
        self.assertFalse(self.app.live_tab.running)

    def finish_live(self):
        deadline = time.monotonic()+3
        while self.app.live_tab.running and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.live_tab.running)

    def test_live_recording_is_independent_of_download_stop_and_keeps_url_private(self):
        from ofdl.common import Cancelled
        from ofdl.live import RecordingResult
        live_started = threading.Event()
        normal_started = threading.Event()
        def normal(control, emit):
            normal_started.set()
            control.wait(60)
        def recording(options, control, emit):
            self.assertEqual(options.proxy,'http://127.0.0.1:8080')
            live_started.set()
            try:
                control.wait(60)
            except Cancelled:
                return RecordingResult(None,0,0,0,'stopped')
        self.app.live_tab.url.set('https://cdn2.onlyfans.com/live.m3u8?secret=private-live-token')
        self.app.proxy.set('http://127.0.0.1:8080')
        with patch('ofdl.ui_live.record_live',side_effect=recording), \
                patch('ofdl.ui_live.messagebox.showerror') as error:
            self.app.run_task('Download fixture',normal)
            self.assertTrue(normal_started.wait(2))
            self.app.live_tab.start()
            self.assertTrue(live_started.wait(2))
            self.assertIsNot(self.app.control,self.app.live_tab.control)
            self.app.stop()
            self.finish_task()
            self.assertTrue(self.app.live_tab.running)
            self.assertFalse(self.app.live_tab.control.stopped.is_set())
            self.app.live_tab.stop()
            self.finish_live()
            error.assert_not_called()
        self.app.save_settings()
        self.assertNotIn('private-live-token',self.app.preferences_path.read_text(encoding='utf-8'))
        self.assertNotIn('private-live-token',self.app.log_text.get('1.0','end'))

    def test_close_waits_for_live_recorder_shutdown(self):
        from ofdl.common import Cancelled
        from ofdl.live import RecordingResult
        started = threading.Event()
        def recording(options, control, emit):
            started.set()
            try:
                control.wait(60)
            except Cancelled:
                return RecordingResult(None,0,0,0,'stopped')
        self.app.live_tab.url.set('https://cdn2.onlyfans.com/live.m3u8')
        with patch('ofdl.ui_live.record_live',side_effect=recording), patch.object(self.root,'destroy') as destroy:
            self.app.live_tab.start()
            self.assertTrue(started.wait(2))
            self.app.close()
            self.assertTrue(self.app.live_tab.control.stopped.is_set())
            destroy.assert_not_called()
            self.finish_live()
            deadline = time.monotonic()+2
            while not destroy.called and time.monotonic()<deadline:
                self.root.update()
                time.sleep(.01)
            destroy.assert_called_once()

    def test_growing_download_queue_does_not_display_fixed_total_eta(self):
        self.app.events.put(('pipeline',{'stage':'streaming'}))
        self.app.events.put(('overall',{'done':3,'total':5,'scanning':True}))
        self.root.after_cancel(self.app.poll_id)
        self.app._poll()
        self.assertIn('found so far',self.app.overall_status.get())
        self.assertEqual(str(self.app.pipeline_progress.cget('mode')),'indeterminate')
        self.app.events.put(('pipeline',{'stage':'download','total':8}))
        self.app.events.put(('overall',{'done':4,'total':8,'scanning':False}))
        self.root.after_cancel(self.app.poll_id)
        self.app._poll()
        self.assertIn('50%',self.app.pipeline_status.get())

    def test_live_auto_opt_in_is_persisted_without_session_or_url(self):
        live = self.app.live_tab
        self.assertFalse(live.enabled.get())
        live.enabled.set(True)
        live.include.set('alice bob')
        live.exclude.set('bob')
        live.interval.set('90')
        live.url.set('https://cdn2.onlyfans.com/private.m3u8?token=hidden')
        live.auto_changed()
        saved = load_preferences(self.app.preferences_path)
        self.assertTrue(saved['live_enabled'])
        self.assertEqual(saved['live_include'],'alice bob')
        self.assertEqual(saved['live_interval'],'90')
        self.assertNotIn('hidden',self.app.preferences_path.read_text())
        live.enabled.set(False)
        live.load_preferences(saved)
        self.assertTrue(live.enabled.get())
        live.maybe_watch()
        self.assertIsNone(live.watch_worker)
        self.assertIn('Waiting',live.watch_status.get())
        live.enabled.set(False)

    def test_auto_watch_snapshots_inputs_and_restarts_after_proxy_change(self):
        from ofdl.live_watch import LiveWatcher
        live = self.app.live_tab
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        self.app.proxy.set('http://127.0.0.1:8080')
        started = threading.Event()
        snapshots = []
        def run(watcher):
            snapshots.append((watcher.auth,watcher.options.proxy))
            started.set()
            try:
                watcher.control.wait(60)
            except Cancelled:
                pass
        with patch.object(LiveWatcher,'run',run):
            live.enabled.set(True)
            live.auto_changed()
            live.maybe_watch()
            self.assertTrue(started.wait(2))
            original = live.watch_control
            self.assertEqual(snapshots[0][1],'http://127.0.0.1:8080')
            self.app.proxy.set('http://127.0.0.1:8081')
            live.next_auto_check = 0
            live.maybe_watch()
            self.assertTrue(original.stopped.is_set())
            live.stop()
            self.finish_live()
        self.assertFalse(live.enabled.get())

    def test_auto_watch_error_is_latched_until_user_retries(self):
        live = self.app.live_tab
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        with patch('ofdl.ui_live.LiveWatcher.run',side_effect=AppError('Session rejected')) as run:
            live.enabled.set(True)
            live.auto_changed()
            live.maybe_watch()
            self.finish_live()
            self.assertEqual(live.watch_status.get(),'Session rejected')
            live.next_auto_check = 0
            live.maybe_watch()
            self.assertEqual(run.call_count,1)
            live.enabled.set(False)

    def test_close_stops_auto_monitor_and_preserves_opt_in(self):
        from ofdl.live_watch import LiveWatcher
        live = self.app.live_tab
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        started = threading.Event()
        def run(watcher):
            started.set()
            try:
                watcher.control.wait(60)
            except Cancelled:
                pass
        with patch.object(LiveWatcher,'run',run),patch.object(self.root,'destroy') as destroy:
            live.enabled.set(True)
            live.auto_changed()
            live.maybe_watch()
            self.assertTrue(started.wait(2))
            self.app.close()
            self.assertTrue(live.watch_control.stopped.is_set())
            self.assertTrue(live.enabled.get())
            self.assertTrue(load_preferences(self.app.preferences_path)['live_enabled'])
            self.finish_live()
            deadline = time.monotonic()+2
            while not destroy.called and time.monotonic()<deadline:
                self.root.update()
                time.sleep(.01)
            destroy.assert_called_once()

    def test_session_fields_are_masked_by_default(self):
        for name in ['user_id','x_bc','sess_cookie']:
            self.assertEqual(self.app.credential_entries[name].cget('show'),'*')

    def test_import_updates_fields_without_logging_secrets(self):
        auth = Credentials('123','UA','bc-private','sess-private')
        self.app.apply_credentials(auth)
        self.root.update()
        self.assertEqual(self.app.auth_vars['sess_cookie'].get(),'sess-private')
        self.assertNotIn('sess-private',self.app.log_text.get('1.0','end'))
        self.assertNotIn('bc-private',self.app.log_text.get('1.0','end'))

    def finish_task(self):
        deadline = time.monotonic()+3
        while self.app.worker is not None and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.assertIsNone(self.app.worker)

    def test_import_and_clear_reset_show_private_values(self):
        for action in [lambda:self.app.apply_credentials(Credentials('123','UA','bc','secret')), self.app.clear_fields]:
            self.app.show_values.set(True)
            self.app.toggle_visibility()
            action()
            self.assertFalse(self.app.show_values.get())
            for field in ['user_id','x_bc','sess_cookie']:
                self.assertEqual(self.app.credential_entries[field].cget('show'), '*')

    def test_session_test_failure_marks_session_not_ready_and_hides_fields(self):
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        self.app.show_values.set(True)
        self.app.toggle_visibility()
        with patch.object(self.app, '_with_client', side_effect=AppError('Safe diagnostic: stage=api; status=400')), patch('ofdl.ui.messagebox.showerror'):
            self.app.test_session()
            self.finish_task()
        self.assertFalse(self.app.show_values.get())
        self.assertIn('not ready', self.app.session_status.get().lower())
        self.assertIn('stage=api; status=400', self.app.log_text.get('1.0','end'))
        self.assertNotIn('secret', self.app.log_text.get('1.0','end'))

    def test_changed_fields_cannot_be_certified_by_in_flight_test(self):
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        done = threading.Event()
        def response(*args):
            done.wait(1)
            return {'id':123}
        with patch.object(self.app, '_with_client', side_effect=response):
            self.app.test_session()
            self.app.auth_vars['sess_cookie'].set('different-session')
            done.set()
            self.finish_task()
        self.assertIn('changed', self.app.session_status.get().lower())
        self.assertNotIn('Session test passed', self.app.log_text.get('1.0','end'))

    def test_editing_after_success_invalidates_session_status(self):
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        with patch.object(self.app, '_with_client', return_value={'id':123}):
            self.app.test_session()
            self.finish_task()
        self.assertIn('accepted', self.app.session_status.get().lower())
        self.app.auth_vars['x_bc'].set('new-bc')
        self.assertIn('test again', self.app.session_status.get().lower())

    def test_worker_result_is_delivered_on_main_thread(self):
        thread_ids = []
        self.app.run_task('Fixture',lambda control,emit: 'result',lambda value: thread_ids.append(threading.get_ident()))
        deadline = time.monotonic()+3
        while self.app.worker is not None and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.assertEqual(thread_ids,[threading.get_ident()])
        self.assertIsNone(self.app.worker)

    def test_success_callback_does_not_leave_busy_status_visible(self):
        self.app.run_task('Testing session',lambda control,emit: True,lambda value: None)
        deadline = time.monotonic()+3
        while self.app.worker is not None and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.assertEqual(self.app.status.get(),'Operation finished')

    def test_download_controls_stay_visible_in_short_window(self):
        self.root.deiconify()
        self.root.geometry('950x650')
        self.root.update()
        start = next(w for w in self.app.action_widgets if w.cget('text') == 'Start download')
        self.assertTrue(start.winfo_ismapped(),'Start button must remain visible')
        top = start.winfo_rooty()-self.root.winfo_rooty()
        self.assertGreaterEqual(top,0)
        self.assertLessEqual(top+start.winfo_height(),self.root.winfo_height())

    def test_short_window_session_tab_can_scroll_to_bottom(self):
        self.root.deiconify()
        self.root.geometry('950x650')
        self.root.update()
        self.assertTrue(hasattr(self.app,'session_canvas'),'Session needs scrolling in short windows')
        canvas = self.app.session_canvas
        self.assertLess(canvas.yview()[1],1.0)
        canvas.yview_moveto(1.0)
        self.root.update()
        self.assertEqual(canvas.yview()[1],1.0)

    def test_unknown_worker_error_never_prints_secret_exception_message(self):
        def fail(control,emit):
            raise RuntimeError('secret-cookie-value')
        with patch('ofdl.ui.messagebox.showerror'):
            self.app.run_task('Fixture',fail)
            deadline = time.monotonic()+3
            while self.app.worker is not None and time.monotonic()<deadline:
                self.root.update()
                time.sleep(.01)
        self.assertNotIn('secret-cookie-value',self.app.log_text.get('1.0','end'))

    def test_activity_tab_has_media_preview_panel(self):
        self.assertTrue(hasattr(self.app, 'preview_image_label'))
        self.assertTrue(hasattr(self.app, 'preview_title'))
        self.assertTrue(hasattr(self.app, 'preview_open_button'))
        self.assertIn('No completed media', self.app.preview_status.get())

    def test_activity_preview_open_button_is_visible_in_standard_window(self):
        self.root.deiconify()
        self.root.geometry('1060x850')
        self.app.tabs.select(self.app.activity_tab)
        media = Path(self.temp.name)/'done.jpg'
        media.write_bytes(b'fake')
        payload = {'path':str(media),'relative':'sample_creator/photos/2026-10-06_123456.jpg',
                   'kind':'photo','profile':'sample_creator','media_id':'123456','size':4}
        class Result:
            kind='image'
            png=None
            message='Image preview unavailable; use Open file.'
        with patch('ofdl.ui.build_preview', return_value=Result()):
            self.app.events.put(('media_complete',payload))
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                self.root.update()
                if ('sample_creator' in self.app.preview_title.get()
                        and not self.app.preview_status.get().startswith('Preparing')):
                    break
                time.sleep(.01)
            self.root.update()
        button = self.app.preview_open_button
        self.assertTrue(button.winfo_ismapped())
        button_bottom = button.winfo_rooty() + button.winfo_height()
        tab_bottom = self.app.activity_tab.winfo_rooty() + self.app.activity_tab.winfo_height()
        self.assertLessEqual(button_bottom, tab_bottom)

    def pump(self, seconds=.4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)

    def test_header_drops_old_subtitle(self):
        labels = [str(w.cget('text')) for w in self.app.header.winfo_children() if w.winfo_class() == 'Label']
        self.assertFalse(any('local interface' in text.lower() for text in labels))
        self.assertTrue(any('DOWNLOADER' in text for text in labels))

    def test_theme_toggle_switches_palette_and_persists(self):
        from ofdl.ui import DARK_PALETTE
        self.assertEqual(self.app.theme_mode.get(), 'light')
        self.app.toggle_theme()
        self.root.update()
        self.assertEqual(self.app.theme_mode.get(), 'dark')
        self.assertEqual(self.app.root.cget('bg'), DARK_PALETTE['window_bg'])
        self.assertEqual(self.app.theme_button.cget('text'), 'Light mode')
        self.assertEqual(load_preferences(self.app.preferences_path).get('theme'), 'dark')
        self.app.toggle_theme()
        self.root.update()
        self.assertEqual(self.app.theme_mode.get(), 'light')

    def test_autoload_preference_persists(self):
        self.assertFalse(self.app.autoload_session.get())
        self.app.autoload_session.set(True)
        self.root.update()
        self.assertTrue(load_preferences(self.app.preferences_path).get('autoload_session'))
        self.app.autoload_session.set(False)

    def test_proxy_credentials_are_never_written_to_preferences(self):
        for url in ('http://user:secret@127.0.0.1:8080',
                    'http://:secret@127.0.0.1:8080',
                    'socks5h://secret@127.0.0.1:1080'):
            with self.subTest(url=url):
                self.app.proxy.set(url)
                self.app.save_settings()
                raw = self.app.preferences_path.read_text(encoding='utf-8')
                self.assertNotIn('secret', raw)
                self.assertFalse(load_preferences(self.app.preferences_path).get('proxy'))
        self.app.proxy.set('socks5h://127.0.0.1:1080')
        self.app.save_settings()
        self.assertEqual(load_preferences(self.app.preferences_path).get('proxy'),
                         'socks5h://127.0.0.1:1080')
        self.app.proxy.set('')

    def test_network_tasks_snapshot_proxy_on_main_thread(self):
        from ofdl.downloads import RunSummary
        auth = Credentials('123','UA','bc','secret')
        self.app.apply_credentials(auth)
        original_get = self.app.proxy.get
        main_thread = threading.get_ident()
        def main_thread_get():
            self.assertEqual(threading.get_ident(), main_thread, 'Tk proxy read from worker')
            return original_get()
        actions = [self.app.test_session, self.app.refresh_rules, self.app.browser_login,
                   self.app.scan_offers, lambda:self.app.start_download(True)]
        for action in actions:
            with self.subTest(action=action):
                self.app.proxy.set('http://127.0.0.1:8080')
                with patch.object(self.app.proxy, 'get', side_effect=main_thread_get), \
                        patch('ofdl.ui.load_rules') as rules, patch('ofdl.ui.ApiClient') as api, \
                        patch('ofdl.ui.capture_session', return_value=auth) as capture, \
                        patch('ofdl.ui.scan_offers', return_value=[]), \
                        patch('ofdl.ui.run_downloads', return_value=RunSummary()), \
                        patch('ofdl.ui.messagebox.askokcancel', return_value=True), \
                        patch('ofdl.ui.messagebox.showerror') as error:
                    api.return_value.__enter__.return_value.user.return_value = {'id':123}
                    action()
                    self.app.proxy.set('http://127.0.0.1:9090')
                    self.finish_task()
                    error.assert_not_called()
                    calls = rules.call_args_list + capture.call_args_list
                    self.assertTrue(calls)
                    self.assertTrue(all(call.kwargs['proxy'] == 'http://127.0.0.1:8080' for call in calls))

    def test_proxy_edit_during_session_test_invalidates_result(self):
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        ready = threading.Event()
        def response(*args):
            ready.wait(1)
            return {'id':123}
        with patch.object(self.app, '_with_client', side_effect=response):
            self.app.test_session()
            self.app.proxy.set('http://127.0.0.1:8080')
            ready.set()
            self.finish_task()
        self.assertIn('changed', self.app.session_status.get())

    def test_cancelled_offer_scan_keeps_new_partial_rows_and_clears_old_scan(self):
        from ofdl.common import Cancelled
        from ofdl.offers import classify, SUBSCRIBED
        old = classify({'id':1,'username':'old','subscribePrice':0}, source='fixture')
        new = classify({'id':2,'username':'new','subscribePrice':0}, source='fixture', status=SUBSCRIBED)
        self.app.offers = {old.key:old}
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        def stopped(client, control, **kwargs):
            kwargs['emit']('offer_row', {'offer':new, 'remove':None})
            raise Cancelled('Stopped fixture')
        def local_client(auth, source, control, emit, work, proxy):
            return work(object())
        with patch.object(self.app, '_with_client', side_effect=local_client), \
                patch('ofdl.ui.scan_offers', side_effect=stopped):
            self.app.scan_offers()
            self.finish_task()
        self.assertEqual(list(self.app.offers), [new.key])
        self.assertEqual(self.app.offer_tree.get_children(), (new.key,))
        self.assertIn('1 partial row(s)', self.app.offer_status.get())
        self.assertFalse(self.app._offer_scanning)
        self.assertTrue(self.app.offer_action_button.instate(['disabled']))

    def test_optional_source_warning_marks_scan_incomplete(self):
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        def incomplete(client, control, **kwargs):
            kwargs['emit']('offer_warning', {'source':'promotions', 'message':'Unavailable'})
            return []
        def local_client(auth, source, control, emit, work, proxy):
            return work(object())
        with patch.object(self.app, '_with_client', side_effect=local_client), \
                patch('ofdl.ui.scan_offers', side_effect=incomplete):
            self.app.scan_offers()
            self.finish_task()
        self.assertIn('Scan incomplete', self.app.offer_status.get())
        self.assertIn('1 source warning(s)', self.app.offer_status.get())

    def test_offer_action_button_enables_for_zero_price_candidates(self):
        from ofdl.offers import classify
        offer = classify({'id':2,'username':'new','subscribePrice':0}, source='fixture')
        self.app.offers = {offer.key:offer}
        self.app.offer_status_filter.set('Available only')
        self.app._refresh_offer_tree()
        self.app.offer_tree.selection_set(offer.key)
        self.root.update()
        self.assertEqual(self.app.offer_tree.get_children(), (offer.key,))
        self.assertFalse(self.app.offer_action_button.instate(['disabled']))
        with patch('ofdl.ui.webbrowser.open') as browser:
            self.app.open_offer_profile()
        browser.assert_called_once_with('https://onlyfans.com/new')

    def test_subscribe_selected_requires_confirmation_and_skips_non_actionable_rows(self):
        from ofdl.offers import classify, SUBSCRIBED
        free = classify({'id':2,'username':'new','subscribePrice':0}, source='fixture')
        used = classify({'id':3,'username':'old','subscribePrice':0}, source='fixture',
                        status=SUBSCRIBED)
        self.app.offers = {free.key:free, used.key:used}
        self.app._refresh_offer_tree()
        self.app.offer_tree.selection_set((free.key,used.key))
        self.root.update()
        self.app.apply_credentials(Credentials('123','UA','bc','secret'))
        claimed = []
        def claim(client, offer, control, history=None, emit=None):
            claimed.append(offer.key)
            offer.status = SUBSCRIBED
            return f'Subscribed to @{offer.username}'
        def local_client(auth, source, control, emit, work, proxy):
            return work(object())
        with patch('ofdl.ui.messagebox.askyesno', return_value=False) as ask:
            self.app.subscribe_selected()
        self.assertIsNone(self.app.worker)
        ask.assert_called_once()
        with patch('ofdl.ui.messagebox.askyesno', return_value=True), \
                patch('ofdl.ui.messagebox.showinfo'), \
                patch('ofdl.ui.claim_offer', side_effect=claim), \
                patch.object(self.app, '_with_client', side_effect=local_client):
            self.app.subscribe_selected()
            self.finish_task()
        self.assertEqual(claimed, [free.key])
        self.assertIn('1 claimed', self.app.offer_status.get())

    def test_offer_search_filters_by_creator_and_source(self):
        from ofdl.offers import classify
        a = classify({'id':2,'username':'alice','subscribePrice':0}, source='fixture')
        b = classify({'id':3,'username':'bob','subscribePrice':0}, source='promotion')
        self.app.offers = {a.key:a, b.key:b}
        self.app.offer_search.set('alice')
        self.app._refresh_offer_tree()
        self.assertEqual(self.app.offer_tree.get_children(), (a.key,))
        self.app.offer_search.set('promotion')
        self.app._refresh_offer_tree()
        self.assertEqual(self.app.offer_tree.get_children(), (b.key,))

    def test_auto_check_schedules_and_cancels(self):
        self.app.auto_check.set(True)
        self.app.auto_check_interval.set('5')
        self.root.update()
        self.assertIsNotNone(self.app._auto_check_id)
        self.app.auto_check_interval.set('0')
        self.root.update()
        self.assertIsNone(self.app._auto_check_id)
        self.app.auto_check_interval.set('30')
        self.root.update()
        self.app.auto_check.set(False)
        self.root.update()
        self.assertIsNone(self.app._auto_check_id)

    def test_pipeline_events_drive_overall_bar_and_eta(self):
        self.app.events.put(('pipeline', {'stage': 'scan'}))
        self.pump()
        self.assertEqual(str(self.app.pipeline_progress.cget('mode')), 'indeterminate')
        self.app.events.put(('pipeline', {'stage': 'download', 'total': 10}))
        self.pump()
        self.app.pipeline_started = time.monotonic() - 5
        self.app.events.put(('overall', {'done': 2, 'total': 10}))
        self.pump()
        self.assertIn('20%', self.app.pipeline_status.get())
        self.assertIn('ETA', self.app.pipeline_status.get())
        self.assertEqual(self.app.overall_status.get(), 'Files: 2/10 complete')
        self.app.events.put(('pipeline', {'stage': 'done'}))
        self.pump()
        self.assertEqual(self.app.pipeline_status.get(), 'Overall progress: finished')

    def test_media_complete_event_updates_preview_without_blocking_poll(self):
        media = Path(self.temp.name)/'done.jpg'
        media.write_bytes(b'fake')
        payload = {'path':str(media),'relative':'creator/photos/done.jpg','kind':'photo',
                   'profile':'creator','media_id':'1','size':4}
        class Result:
            kind='image'
            png=None
            message='Preview unavailable in fixture'
        with patch('ofdl.ui.build_preview', return_value=Result()):
            self.app.events.put(('media_complete',payload))
            deadline = time.monotonic()+2
            while 'unavailable' not in self.app.preview_status.get().lower() and time.monotonic()<deadline:
                self.root.update()
                time.sleep(.01)
        self.assertIn('creator', self.app.preview_title.get())
        self.assertIn('done.jpg', self.app.preview_details.get())
        self.assertIn('unavailable', self.app.preview_status.get().lower())
        self.assertEqual(self.app.preview_path, media)


if __name__ == '__main__':
    unittest.main()
