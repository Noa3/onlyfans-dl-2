"""Creator-scope and date-control regressions; no real account or network calls."""
import json
import os
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ofdl.auth import Credentials
from ofdl.common import AppError, Cancelled, Control
from ofdl.downloads import Options, run_downloads
from ofdl.settings import load_preferences, save_preferences
from ofdl.ui import App


class SubscriptionClient:
    """Finite account/content fixture, recording the creators actually queried."""
    auth = Credentials('123', 'TestBrowser', 'test-bc', 'test-session')

    def __init__(self, names=('alpha', 'beta', 'gamma')):
        self.names = list(names)
        self.lookups = []
        self.subscription_calls = 0

    def user(self, name):
        self.lookups.append(name)
        return {'id': 123 if name == 'me' else 42 + ['alpha', 'beta', 'gamma'].index(name),
                'username': name}

    def paginate(self, endpoint, kind, **kwargs):
        if kind == 'subscriptions':
            self.subscription_calls += 1
            for name in self.names:
                yield {'username': name}
        elif kind == 'posts':
            yield {'id': 1, 'postedAt': '2026-10-01T00:00:00Z',
                   'media': [{'id': 9, 'type': 'photo', 'canView': True,
                              'files': {'full': {'url': 'https://cdn2.onlyfans.com/9.jpg'}}}]}


class CreatorResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'not-created'
        self.client = SubscriptionClient()

    def options(self, **changes):
        values = dict(output_dir=self.output, profiles=[], categories=('posts',),
                      dry_run=True, all_subscriptions=True)
        values.update(changes)
        return Options(**values)

    def test_automatic_mode_download_scope_is_all_active_subscriptions(self):
        summary = run_downloads(self.client, self.options(), Control())
        self.assertEqual(self.client.lookups, ['me', 'alpha', 'beta', 'gamma'])
        self.assertEqual(self.client.subscription_calls, 1)
        self.assertEqual(summary.planned, 3)
        self.assertFalse(self.output.exists(), 'Scan only must not write media/manifest')

    def test_skip_applies_to_automatic_mode_case_insensitively(self):
        summary = run_downloads(self.client, self.options(skip_profiles=('@BeTa',)), Control())
        self.assertEqual(self.client.lookups, ['me', 'alpha', 'gamma'])
        self.assertEqual(summary.planned, 2)

    def test_skip_applies_to_specific_list_without_loading_subscriptions(self):
        opts = self.options(all_subscriptions=False, profiles=['ALPHA', 'beta'],
                            skip_profiles=('https://onlyfans.com/beta',))
        summary = run_downloads(self.client, opts, Control())
        self.assertEqual(self.client.lookups, ['me', 'alpha'])
        self.assertEqual(self.client.subscription_calls, 0)
        self.assertEqual(summary.planned, 1)

    def test_duplicate_subscriptions_are_queried_once(self):
        self.client.names = ['ALPHA', 'alpha', 'beta']
        run_downloads(self.client, self.options(), Control())
        self.assertEqual(self.client.lookups, ['me', 'alpha', 'beta'])

    def test_no_subscriptions_does_not_fall_back_to_old_manual_names(self):
        self.client.names = []
        with self.assertRaisesRegex(AppError, 'No active subscriptions'):
            run_downloads(self.client, self.options(profiles=['alpha']), Control())
        self.assertEqual(self.client.lookups, ['me'])

    def test_all_skipped_reports_empty_scope_instead_of_downloading_all(self):
        with self.assertRaisesRegex(AppError, 'No creators remain'):
            run_downloads(self.client, self.options(skip_profiles=('alpha', 'beta', 'gamma')), Control())
        self.assertEqual(self.client.lookups, ['me'])
        self.assertFalse(self.output.exists())

    def test_empty_manual_list_never_defaults_to_all(self):
        with self.assertRaisesRegex(AppError, 'at least one creator'):
            self.options(all_subscriptions=False).validate()
        self.assertEqual(self.client.subscription_calls, 0)

    def test_manual_list_fully_skipped_fails_validation(self):
        with self.assertRaisesRegex(AppError, 'No creators remain'):
            self.options(all_subscriptions=False, profiles=['alpha'],
                         skip_profiles=('alpha',)).validate()

    def test_each_automatic_run_refreshes_subscriptions(self):
        opts = self.options()
        run_downloads(self.client, opts, Control())
        self.client.names = ['gamma']
        self.client.lookups.clear()
        run_downloads(self.client, opts, Control())
        self.assertEqual(self.client.lookups, ['me', 'gamma'])
        self.assertEqual(self.client.subscription_calls, 2)
        self.assertEqual(opts.profiles, [], 'Do not persist a stale subscription snapshot')

    def test_partial_subscription_failure_never_starts_creator_scan(self):
        class FailingClient(SubscriptionClient):
            def paginate(self, endpoint, kind, **kwargs):
                yield {'username': 'alpha'}
                raise AppError('Subscription page failed')
        client = FailingClient()
        with self.assertRaisesRegex(AppError, 'Subscription page failed'):
            run_downloads(client, self.options(), Control())
        self.assertEqual(client.lookups, ['me'])
        self.assertFalse(self.output.exists())

    def test_cancellation_during_subscription_discovery_stops_scan(self):
        control = Control()
        class CancellingClient(SubscriptionClient):
            def paginate(self, endpoint, kind, **kwargs):
                control.stop()
                yield {'username': 'alpha'}
        client = CancellingClient()
        with self.assertRaises(Cancelled):
            run_downloads(client, self.options(), control)
        self.assertEqual(client.lookups, ['me'])

    def test_invalid_subscription_username_is_not_silently_ignored(self):
        self.client.names = [None]
        with self.assertRaisesRegex(AppError, 'subscription.*username'):
            run_downloads(self.client, self.options(), Control())
        self.assertEqual(self.client.lookups, ['me'])

    def test_automatic_download_writes_files_only_for_non_skipped_creators(self):
        from ofdl.downloads import Downloader
        from test_api import session_for
        session, adapter = session_for([(200, b'fixture', {'Content-Type': 'image/jpeg', 'Content-Length': '7'}),
                                        (200, b'fixture', {'Content-Type': 'image/jpeg', 'Content-Length': '7'})])
        def downloader(options, user_agent, control, manifest, *, emit):
            return Downloader(options, user_agent, control, manifest, emit=emit, session=session, retries=0)
        with patch('ofdl.downloads.Downloader', side_effect=downloader):
            summary = run_downloads(self.client, self.options(skip_profiles=('beta',), dry_run=False), Control())
        self.assertEqual(summary.downloaded, 2)
        self.assertEqual(len(adapter.requests), 2)
        self.assertEqual((self.output / 'alpha/photos/2026-10-01_9.jpg').read_bytes(), b'fixture')
        self.assertEqual((self.output / 'gamma/photos/2026-10-01_9.jpg').read_bytes(), b'fixture')
        self.assertFalse((self.output / 'beta').exists())

    def test_activity_reports_actual_included_and_skipped_counts(self):
        events = []
        run_downloads(self.client, self.options(skip_profiles=('beta', 'not_subscribed')),
                      Control(), lambda kind, value: events.append((kind, value)))
        messages = ' '.join(value for kind, value in events if kind == 'log')
        self.assertIn('2 included', messages)
        self.assertIn('1 skipped', messages)


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'A graphical display is required')
class DownloadControlsTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = App(self.root, preferences_path=self.path)
        self.root.update()

    def set_names(self, text):
        self.app.creator_mode.set('only')
        self.app.profiles_text.delete('1.0', 'end')
        self.app.profiles_text.insert('1.0', text)
        self.root.update()

    def reload_preferences(self, values):
        self.path.write_text(json.dumps(values), encoding='utf-8')
        self.app._load_preferences()
        self.root.update()

    def finish_task(self):
        deadline = time.monotonic() + 3
        while self.app.worker is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertIsNone(self.app.worker)

    def test_default_accepts_empty_creator_box_as_all_subscriptions(self):
        options = self.app.get_options(True)
        self.assertTrue(options.all_subscriptions)
        self.assertEqual(options.profiles, [])
        self.assertEqual(options.skip_profiles, ())
        self.assertIsNone(self.app.worker, 'Opening the app must not start requests')
        self.assertIn('All active subscriptions', self.app.creator_summary.get())

    def test_old_selection_popup_button_is_removed(self):
        labels = [str(w.cget('text')) for w in self.app.action_widgets]
        self.assertNotIn('Load subscriptions', labels)
        self.assertFalse(hasattr(self.app, 'subscription_picker'))

    def test_days_and_date_are_disabled_for_all_dates(self):
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))
        self.assertTrue(self.app.since_entry.instate(['disabled']))
        self.assertEqual(str(self.app.profiles_text.cget('state')), 'disabled')

    def test_only_days_enabled_for_last_n_days(self):
        self.app.date_mode.set('Last N days')
        self.assertFalse(self.app.days_spinbox.instate(['disabled']))
        self.assertTrue(self.app.since_entry.instate(['disabled']))
        self.app.days.set('7')
        age = datetime.now(timezone.utc) - self.app.get_options(True).since
        self.assertAlmostEqual(age.total_seconds(), 7 * 86400, delta=2)

    def test_only_calendar_date_enabled_for_since_date(self):
        self.app.date_mode.set('Since date')
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))
        self.assertFalse(self.app.since_entry.instate(['disabled']))
        self.app.since.set('2026-10-01')
        self.app.days.set('not needed')
        self.assertEqual(self.app.get_options(True).since, datetime(2026, 10, 1, tzinfo=timezone.utc))

    def test_inactive_date_values_are_ignored_not_validated(self):
        self.app.days.set('not a number')
        self.app.since.set('not a date')
        self.assertIsNone(self.app.get_options(True).since)
        self.app.date_mode.set('Last N days')
        with self.assertRaisesRegex(AppError, 'Days must'):
            self.app.get_options(True)

    def test_switching_filters_preserves_values_without_using_them(self):
        self.app.days.set('30')
        self.app.since.set('2026-09-01')
        self.app.date_mode.set('Since date')
        self.app.date_mode.set('All dates')
        self.assertIsNone(self.app.get_options(True).since)
        self.assertEqual(self.app.days.get(), '30')
        self.assertEqual(self.app.since.get(), '2026-09-01')
        self.app.date_mode.set('Last N days')
        self.assertFalse(self.app.days_spinbox.instate(['disabled']))

    def test_specific_mode_enables_box_and_does_not_fall_back_when_empty(self):
        self.app.creator_mode.set('only')
        self.assertEqual(str(self.app.profiles_text.cget('state')), 'normal')
        with self.assertRaisesRegex(AppError, 'at least one creator'):
            self.app.get_options(True)
        self.assertIn('No creators', self.app.creator_summary.get())

    def test_manual_summary_displays_effective_inclusions_and_exclusions(self):
        self.set_names('Alpha, @beta\nalpha')
        self.app.skip_profiles.set('BETA, not_subscribed')
        summary = self.app.creator_summary.get()
        self.assertIn('1 included', summary)
        self.assertIn('1 skipped', summary)
        self.assertIn('alpha', summary)
        opts = self.app.get_options(True)
        self.assertFalse(opts.all_subscriptions)
        self.assertEqual(opts.skip_profiles, ('beta', 'not_subscribed'))

    def test_disabled_manual_text_does_not_limit_automatic_scope(self):
        self.set_names('alpha')
        self.app.creator_mode.set('all')
        self.app.skip_profiles.set('beta')
        opts = self.app.get_options(True)
        self.assertTrue(opts.all_subscriptions)
        self.assertEqual(opts.profiles, [])
        self.assertEqual(opts.skip_profiles, ('beta',))
        self.assertIn('1', self.app.creator_summary.get())
        self.app.creator_mode.set('only')
        self.assertEqual(self.app.profiles_text.get('1.0', 'end-1c'), 'alpha')

    def test_bad_skip_input_is_visible_and_blocks_start(self):
        self.app.skip_profiles.set('https://example.com/private')
        self.assertIn('Check Skip creators', self.app.creator_summary.get())
        with self.assertRaises(AppError):
            self.app.get_options(True)

    def test_new_preferences_preserve_zero_in_dormant_days_field(self):
        self.app.days.set('0')
        self.app.save_settings()
        self.app._load_preferences()
        self.assertEqual(self.app.days.get(), '0', 'Only legacy zero mode flags should migrate to seven')
        self.assertIsNone(self.app.get_options(True).since)
        self.app.date_mode.set('Last N days')
        with self.assertRaisesRegex(AppError, 'Days must'):
            self.app.get_options(True)

    def test_intro_does_not_claim_all_creators_when_only_mode_is_selected(self):
        self.set_names('alpha')
        labels = [str(w.cget('text')) for w in self.app.downloads_content.winfo_children()
                  if w.winfo_class() == 'TLabel']
        self.assertFalse(any('All active subscriptions are included automatically' in t for t in labels))

    def test_disabled_inputs_cannot_be_edited(self):
        self.app.days_spinbox.delete(0, 'end')
        self.app.days_spinbox.insert(0, '999')
        self.app.profiles_text.insert('1.0', 'alpha')
        self.assertEqual(self.app.days.get(), '7')
        self.assertEqual(self.app.profiles_text.get('1.0', 'end-1c'), '')

    def test_restart_restores_explicit_modes_and_disabled_states(self):
        import tkinter as tk
        self.set_names('alpha, beta')
        self.app.creator_mode.set('all')
        self.app.days.set('30')
        self.app.save_settings()
        other_root = tk.Tk()
        other_root.withdraw()
        try:
            other_app = App(other_root, preferences_path=self.path)
            other_root.update()
            self.assertTrue(other_app.get_options(True).all_subscriptions)
            self.assertIsNone(other_app.get_options(True).since)
            self.assertEqual(str(other_app.profiles_text.cget('state')), 'disabled')
            self.assertTrue(other_app.days_spinbox.instate(['disabled']))
        finally:
            other_root.destroy()

    def test_short_downloads_tab_can_scroll_to_date_controls(self):
        self.root.deiconify()
        self.root.geometry('950x650')
        self.app.tabs.select(self.app.downloads_tab)
        self.root.update()
        canvas = self.app.downloads_canvas
        self.assertLess(canvas.yview()[1], 1.0)
        canvas.yview_moveto(1.0)
        self.root.update()
        self.assertEqual(canvas.yview()[1], 1.0)
        for w in (self.app.date_choice, self.app.days_spinbox, self.app.since_entry):
            self.assertGreaterEqual(w.winfo_rooty(), canvas.winfo_rooty())
            self.assertLessEqual(w.winfo_rooty()+w.winfo_height(), canvas.winfo_rooty()+canvas.winfo_height())
            self.assertLessEqual(w.winfo_rootx()+w.winfo_width(), canvas.winfo_rootx()+canvas.winfo_width())

    def test_saved_all_mode_retains_dormant_values_but_never_activates_them(self):
        self.set_names('alpha')
        self.app.creator_mode.set('all')
        self.app.days.set('30')
        self.app.since.set('2026-09-01')
        self.app.save_settings()
        saved = load_preferences(self.path)
        self.assertEqual(saved['creator_mode'], 'all')
        self.assertEqual(saved['date_mode'], 'All dates')
        self.assertEqual(saved['days'], '30')
        self.assertEqual(saved['since'], '2026-09-01')
        self.app._load_preferences()
        self.assertEqual(self.app.date_mode.get(), 'All dates')
        self.assertTrue(self.app.get_options(True).all_subscriptions)
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))

    def test_legacy_explicit_list_is_preserved_as_visible_only_mode(self):
        self.reload_preferences({'profiles': 'alpha\nbeta', 'days': '0', 'since': ''})
        self.assertEqual(self.app.creator_mode.get(), 'only')
        self.assertEqual(self.app.get_options(True).profiles, ['alpha', 'beta'])
        self.assertEqual(self.app.date_mode.get(), 'All dates')
        self.assertEqual(self.app.days.get(), '7', 'Legacy zero is a mode flag, not usable Days')
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))

    def test_legacy_empty_list_defaults_to_automatic_mode(self):
        self.reload_preferences({'profiles': '  ', 'days': '0'})
        self.assertTrue(self.app.get_options(True).all_subscriptions)

    def test_legacy_date_modes_are_migrated(self):
        self.reload_preferences({'days': '14'})
        self.assertEqual(self.app.date_mode.get(), 'Last N days')
        self.assertFalse(self.app.days_spinbox.instate(['disabled']))
        self.reload_preferences({'days': '0', 'since': '2026-10-01'})
        self.assertEqual(self.app.date_mode.get(), 'Since date')
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))
        self.assertFalse(self.app.since_entry.instate(['disabled']))

    def test_worker_completion_does_not_enable_unused_inputs(self):
        done = threading.Event()
        self.app.run_task('Fixture', lambda control, emit: done.wait(1))
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))
        done.set()
        self.finish_task()
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))
        self.assertTrue(self.app.since_entry.instate(['disabled']))
        self.assertEqual(str(self.app.profiles_text.cget('state')), 'disabled')
        self.assertFalse(self.app.skip_entry.instate(['disabled']))

    def test_active_date_field_returns_to_enabled_after_worker(self):
        self.app.date_mode.set('Last N days')
        done = threading.Event()
        self.app.run_task('Fixture', lambda control, emit: done.wait(1))
        self.assertTrue(self.app.days_spinbox.instate(['disabled']))
        done.set()
        self.finish_task()
        self.assertFalse(self.app.days_spinbox.instate(['disabled']))
        self.assertTrue(self.app.since_entry.instate(['disabled']))

    def test_scan_button_resolves_all_minus_skip_without_a_picker(self):
        self.app.apply_credentials(SubscriptionClient.auth)
        self.app.skip_profiles.set('beta')
        self.app.output_dir.set(str(Path(self.temp.name) / 'no-output'))
        client = SubscriptionClient()
        def local_client(auth, source, control, emit, work):
            return work(client)
        with patch.object(self.app, '_with_client', side_effect=local_client), patch('ofdl.ui.messagebox.showerror') as error:
            self.app.start_download(True)
            self.finish_task()
        error.assert_not_called()
        self.assertEqual(client.lookups, ['me', 'alpha', 'gamma'])
        self.assertIn('Scan complete', self.app.status.get())
        self.assertFalse(Path(self.app.output_dir.get()).exists())
        self.assertFalse(any(w.winfo_class() == 'Toplevel' for w in self.root.winfo_children()))

    def test_scope_is_snapshotted_before_worker_runs(self):
        self.app.apply_credentials(SubscriptionClient.auth)
        self.app.skip_profiles.set('beta')
        ready = threading.Event()
        client = SubscriptionClient()
        def local_client(auth, source, control, emit, work):
            ready.wait(1)
            return work(client)
        with patch.object(self.app, '_with_client', side_effect=local_client), patch('ofdl.ui.messagebox.showerror') as error:
            self.app.start_download(True)
            self.app.skip_profiles.set('alpha')
            ready.set()
            self.finish_task()
        error.assert_not_called()
        self.assertEqual(client.lookups, ['me', 'alpha', 'gamma'])


class ChoicePreferenceTests(unittest.TestCase):
    def test_mode_preferences_allowed_while_auth_stays_excluded(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            save_preferences({'creator_mode': 'all', 'date_mode': 'All dates',
                              'sess_cookie': 'never-save-this', 'days': '7'}, path)
            values = load_preferences(path)
            self.assertEqual(values['creator_mode'], 'all')
            self.assertEqual(values['date_mode'], 'All dates')
            self.assertNotIn('never-save-this', path.read_text())


if __name__ == '__main__':
    unittest.main()
