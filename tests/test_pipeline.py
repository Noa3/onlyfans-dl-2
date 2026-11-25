"""Concurrent discovery/download regressions with deterministic hand-off events."""
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ofdl.auth import Credentials
from ofdl.api import ApiError
from ofdl.common import AppError, Cancelled, Control
from ofdl.downloads import Downloader, FolderLock, Options, run_downloads
from ofdl.pipeline import BoundedWorker
from test_api import session_for


def post(media_id=1):
    return {'id':media_id,'media':[{'id':media_id,'type':'photo',
                                  'files':{'full':{'url':f'https://cdn2.onlyfans.com/{media_id}.jpg'}}}]}


class Client:
    auth = Credentials('123','fixture','bc','secret')
    proxy = ''

    def user(self, name):
        return {'id':123 if name == 'me' else 42,'username':name}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'library'
        self.options = Options(self.root,['creator'],categories=('posts',))
        self.control = Control()

    def test_download_finishes_before_discovery_finishes_and_dedupes_later_pages(self):
        downloaded = threading.Event()
        outer = self
        class StreamingClient(Client):
            def paginate(self, *args, **kwargs):
                yield post(1)
                outer.assertTrue(downloaded.wait(3), 'First download waited for all discovery')
                yield post(1)
                yield post(2)
        events = []
        consumer_threads = []
        session, adapter = session_for([(200,b'first',{'Content-Type':'image/jpeg'}),
                                        (200,b'second',{'Content-Type':'image/jpeg'})])
        def factory(options, user_agent, control, manifest, **kwargs):
            consumer_threads.append(threading.get_ident())
            return Downloader(options,user_agent,control,manifest,session=session,retries=0,**kwargs)
        def emit(kind, value):
            events.append((kind,value))
            if kind == 'stats' and value['downloaded'] == 1:
                downloaded.set()
        with patch('ofdl.downloads.Downloader', side_effect=factory):
            summary = run_downloads(StreamingClient(),self.options,self.control,emit)
        self.assertEqual((summary.planned,summary.downloaded,summary.duplicates), (2,2,1))
        self.assertEqual(len(adapter.requests), 2)
        self.assertNotEqual(consumer_threads[0], threading.get_ident())
        self.assertTrue(any(kind == 'overall' and value['scanning'] and value['done'] == 1 for kind,value in events))
        self.assertEqual([value for kind,value in events if kind == 'overall'][-1],
                         {'done':2,'total':2,'scanning':False})
        with FolderLock(self.root):
            pass

    def test_fatal_discovery_error_keeps_downloaded_file_and_releases_resources(self):
        downloaded = threading.Event()
        class FailingClient(Client):
            def paginate(self, *args, **kwargs):
                yield post()
                if not downloaded.wait(3):
                    raise AssertionError('Download did not overlap discovery')
                raise ApiError(401)
        session, _ = session_for([(200,b'kept',{'Content-Type':'image/jpeg'})])
        def factory(options, user_agent, control, manifest, **kwargs):
            return Downloader(options,user_agent,control,manifest,session=session,retries=0,**kwargs)
        def emit(kind, value):
            if kind == 'stats' and value['downloaded']:
                downloaded.set()
        with patch('ofdl.downloads.Downloader', side_effect=factory):
            with self.assertRaises(ApiError):
                run_downloads(FailingClient(),self.options,self.control,emit)
        self.assertEqual(next(self.root.rglob('*.jpg')).read_bytes(), b'kept')
        with FolderLock(self.root):
            pass

    def test_worker_startup_failure_reaches_caller_without_deadlock(self):
        class ManyClient(Client):
            def paginate(self, *args, **kwargs):
                for number in range(200):
                    yield post(number)
        with patch('ofdl.downloads.Downloader', side_effect=AppError('Worker startup failed')):
            with self.assertRaisesRegex(AppError,'Worker startup failed'):
                run_downloads(ManyClient(),self.options,self.control)
        with FolderLock(self.root):
            pass

    def test_bounded_queue_cancellation_unblocks_producer(self):
        consuming = threading.Event()
        def consume(items):
            next(items)
            consuming.set()
            self.control.wait(60)
        worker = BoundedWorker(self.control,consume,capacity=1)
        worker.submit(1)
        self.assertTrue(consuming.wait(2))
        worker.submit(2)
        self.assertEqual(worker.pending.qsize(), 1)
        def cancel():
            self.control.stop()
        stopper = threading.Thread(target=cancel)
        stopper.start()
        stopper.join()
        with self.assertRaises(Cancelled):
            with worker:
                worker.submit(3)
        self.assertFalse(worker.thread.is_alive())

    def test_empty_and_scan_only_runs_do_not_start_media_worker(self):
        class EmptyClient(Client):
            def paginate(self, *args, **kwargs):
                return iter([])
        with patch('ofdl.downloads.Downloader') as worker:
            run_downloads(EmptyClient(),self.options,self.control)
            self.options.dry_run = True
            run_downloads(EmptyClient(),self.options,Control())
        worker.assert_not_called()
        self.assertFalse(self.root.exists())
