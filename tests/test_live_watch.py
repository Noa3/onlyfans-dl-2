import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ofdl.api import ApiClient, ApiError, Rules
from ofdl.common import AppError, Cancelled, Control
from ofdl.live import RecordingResult
from ofdl.live_watch import LiveStream, LiveWatcher, WatchOptions, discover_streams
from test_api import AUTH, RULE_DATA, session_for


class DiscoveryTests(unittest.TestCase):
    def client(self, replies):
        session, adapter = session_for([(status, data, {}) for status,data in replies])
        client = ApiClient(AUTH, Rules.from_dict(RULE_DATA), Control(), session=session, interval=0, retries=0)
        self.addCleanup(client.close)
        return client, adapter

    def test_live_detection_is_signed_get_only_and_respects_filters(self):
        rows = [{'id':n,'username':name,'hasStream':live} for n,name,live in
                [(1,'alice',True),(2,'bob',True),(3,'carol',False),(4,'dave',True)]]
        client, adapter = self.client([(200,rows),(200,{'id':10,'isActive':True,'isFinished':False})])
        streams = discover_streams(client, WatchOptions(Path('.'),include='alice bob carol',exclude='bob'))
        self.assertEqual(streams, [LiveStream('alice','10')])
        self.assertEqual(len(adapter.requests),2)
        for request in adapter.requests:
            self.assertEqual(request.method,'GET')
            self.assertIn('sign',request.headers)
        self.assertIn('type=active',adapter.requests[0].url)
        self.assertIn('/users/alice/streams/active?view=full_with_check',adapter.requests[1].url)

    def test_paging_handles_all_subscriptions_before_reporting_success(self):
        client, adapter = self.client([(200,{'list':[{'id':1,'username':'a','hasStream':False}],'hasMore':True}),
                                      (200,{'list':[{'id':2,'username':'b','hasStream':True}],'hasMore':False}),
                                      (200,{'id':20,'isActive':True})])
        self.assertEqual(discover_streams(client,WatchOptions(Path('.'))),[LiveStream('b','20')])
        self.assertIn('offset=1',adapter.requests[1].url)

    def test_missing_status_or_bad_stream_schema_is_not_offline(self):
        for row, detail in [({'id':1,'username':'a'}, None),
                            ({'id':1,'username':'a','hasStream':True}, {'id':20}),
                            ({'id':1,'username':'a','hasStream':True}, {'id':'../x','isActive':True})]:
            replies = [(200,[row])] + ([(200,detail)] if detail else [])
            client,_ = self.client(replies)
            with self.assertRaises(AppError):
                discover_streams(client,WatchOptions(Path('.')))

    def test_finished_or_disappeared_stream_is_ignored(self):
        for response in [(404,{}),(200,None),(200,{'id':20,'isActive':True,'isFinished':True})]:
            client,_ = self.client([(200,[{'id':1,'username':'a','hasStream':True}]),response])
            self.assertEqual(discover_streams(client,WatchOptions(Path('.'))),[])

    def test_auth_failure_and_rate_limit_propagate(self):
        for status in (401,403,429):
            client,_ = self.client([(status,{})])
            with self.assertRaises(ApiError) as error:
                discover_streams(client,WatchOptions(Path('.')))
            self.assertEqual(error.exception.status,status)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.options = WatchOptions(Path(self.temp.name),parallel=1)
        self.control = Control()
        self.now = 1000
        self.events = []
        self.calls = []

    def watcher(self, recorder):
        watcher = LiveWatcher(AUTH,'rules',self.options,self.control,
                              lambda k,v:self.events.append((k,v)),recorder=recorder,
                              clock=lambda:self.now)
        def cleanup():
            self.control.stop()
            for _,control in watcher.jobs.values():
                control.stop()
            for thread,_ in watcher.jobs.values():
                thread.join(3)
        self.addCleanup(cleanup)
        return watcher

    def success(self, stream, auth, options, control, emit):
        self.calls.append(stream.key)
        return RecordingResult(options.output_dir/'test.webm',1,3,100,'ended')

    def settle(self, watcher):
        for thread,_ in list(watcher.jobs.values()):
            thread.join(3)
            self.assertFalse(thread.is_alive())
        watcher._drain()

    def test_success_is_deduplicated_but_new_stream_id_records(self):
        watcher = self.watcher(self.success)
        stream = LiveStream('a','10')
        watcher.step([stream,stream])
        self.settle(watcher)
        watcher.step([stream])
        self.assertEqual(self.calls,['a:10'])
        watcher.step([LiveStream('a','11')])
        self.settle(watcher)
        self.assertEqual(self.calls,['a:10','a:11'])

    def test_parallel_limit_and_stop_selected_suppress_same_stream(self):
        started = threading.Event()
        def record(stream,auth,options,control,emit):
            self.calls.append(stream.key)
            started.set()
            control.wait(60)
        watcher = self.watcher(record)
        a,b = LiveStream('a','1'),LiveStream('b','2')
        watcher.step([a,b])
        self.assertTrue(started.wait(2))
        self.assertEqual(len(watcher.jobs),1)
        self.assertIn('waiting',watcher.rows[b.key]['status'])
        watcher.stop_selected([a.key])
        # Immediate even before supervisor can drain commands after an API call.
        self.assertTrue(watcher.job_controls[a.key].stopped.is_set())
        watcher._drain()
        self.settle(watcher)
        started.clear()
        watcher.step([a,b])
        self.assertTrue(started.wait(2))
        self.assertEqual(self.calls,['a:1','b:2'])

    def test_failures_have_cooldown_and_stop_after_three_attempts(self):
        def fail(*args):
            self.calls.append(1)
            raise AppError('Temporary capture failure')
        watcher = self.watcher(fail)
        streams = [LiveStream('a','1')]
        watcher.step(streams)
        self.settle(watcher)
        watcher.step(streams)
        self.assertEqual(len(self.calls),1)
        self.now += 60
        watcher.step(streams)
        self.settle(watcher)
        self.now += 120
        watcher.step(streams)
        self.settle(watcher)
        self.now += 10000
        watcher.step(streams)
        self.assertEqual(len(self.calls),3)
        self.assertIn('retry limit',watcher.rows['a:1']['status'])

    def test_run_shutdown_joins_every_recording(self):
        started = threading.Event()
        def record(stream,auth,options,control,emit):
            started.set()
            control.wait(60)
        watcher = self.watcher(record)
        watcher.discover = lambda:[LiveStream('a','1')]
        thread = threading.Thread(target=watcher.run)
        thread.start()
        self.assertTrue(started.wait(2))
        self.control.stop()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(watcher.jobs)

    def test_two_successful_offline_scans_stop_recording(self):
        started = threading.Event()
        def record(stream,auth,options,control,emit):
            started.set()
            control.wait(60)
        watcher = self.watcher(record)
        stream = LiveStream('a','1')
        watcher.step([stream])
        self.assertTrue(started.wait(2))
        job_control = watcher.jobs[stream.key][1]
        watcher.snapshot([])
        self.assertFalse(job_control.stopped.is_set())
        watcher.snapshot([stream])
        watcher.snapshot([])
        self.assertFalse(job_control.stopped.is_set())
        watcher.snapshot([])
        self.assertTrue(job_control.stopped.is_set())
        self.settle(watcher)

    def test_run_rate_limit_backs_off_without_rapid_retries(self):
        watcher = self.watcher(self.success)
        def discover():
            self.calls.append(self.now)
            raise ApiError(429)
        watcher.discover = discover
        def wait(seconds):
            self.now += 1
            if self.now >= 1005:
                self.control.stop()
        self.control.wait = wait
        watcher.run()
        self.assertEqual(len(self.calls),1)
        self.assertTrue(any('retry in 300s' in str(value) for _,value in self.events))

    def test_auth_rejection_stops_monitor_with_actionable_message(self):
        watcher = self.watcher(self.success)
        def discover():
            raise ApiError(401)
        watcher.discover = discover
        with self.assertRaisesRegex(AppError,'session rejected'):
            watcher.run()

    def test_invalid_limits_and_nonfinite_duration_rejected(self):
        for values in [{'interval':0},{'parallel':9},{'max_seconds':float('nan')},
                       {'max_seconds':float('inf')},{'include':'../oops'}]:
            with self.subTest(values=values),self.assertRaises(AppError):
                WatchOptions(Path('.'),**values).validate()
