import io
import json
import os
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from test_api import session_for
from ofdl.common import AppError, Cancelled, Control
from ofdl.auth import Credentials

try:
    from ofdl.downloads import (Options, MediaTask, Downloader, Manifest, RunSummary,
                               parse_profiles, media_task, run_downloads, safe_component,
                               validate_media_url, FolderLock)
except ImportError:
    Options = MediaTask = Downloader = Manifest = RunSummary = parse_profiles = media_task = run_downloads = safe_component = validate_media_url = FolderLock = None


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Downloader, 'Download engine is not implemented')
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = Options(output_dir=self.root, profiles=['creator'], albums=False)

    def task(self, source='https://cdn2.onlyfans.com/files/123.jpg', **changes):
        values = dict(owner_id='42', profile='creator', media_id='123', kind='photo',
                      category='posts', date='2026-10-01', album='', url=source, preview=False)
        values.update(changes)
        return MediaTask(**values)

    def downloader(self, replies, options=None):
        session, adapter = session_for(replies)
        manifest = Manifest(self.root)
        self.addCleanup(manifest.close)
        worker = Downloader(options or self.options, 'TestBrowser/1.0', Control(), manifest,
                            session=session, retries=0)
        self.addCleanup(worker.close)
        return worker, adapter, manifest

    def test_completed_download_is_atomic_and_indexed(self):
        worker, adapter, manifest = self.downloader([(200,b'abcdef',{'Content-Type':'image/jpeg','Content-Length':'6','ETag':'"v1"'})])
        result = worker.download(self.task())
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        self.assertEqual(result,('downloaded',6))
        self.assertEqual(path.read_bytes(),b'abcdef')
        self.assertFalse(path.with_suffix('.jpg.part').exists())
        self.assertEqual(manifest.find(self.task()),path)
        self.assertTrue(adapter.options[0]['verify'])
        self.assertNotIn('Cookie',adapter.requests[0].headers)
        self.assertNotIn('x-bc',adapter.requests[0].headers)

    def test_existing_nonempty_legacy_file_skipped_without_request(self):
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous')
        worker, adapter, _ = self.downloader([])
        self.assertEqual(worker.download(self.task()),('existing',0))
        self.assertFalse(adapter.requests)

    def test_zero_byte_existing_file_is_redownloaded(self):
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        path.parent.mkdir(parents=True)
        path.touch()
        worker, _, _ = self.downloader([(200,b'new',{'Content-Type':'image/jpeg','Content-Length':'3'})])
        worker.download(self.task())
        self.assertEqual(path.read_bytes(),b'new')

    def test_deferred_manifest_record_is_persisted_on_close(self):
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'existing')
        manifest = Manifest(self.root)
        manifest.record(self.task(), path, durable=False)
        manifest.close()
        reopened = Manifest(self.root)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.find(self.task()), path)

    def test_manifest_deduplicates_across_content_folders(self):
        worker, adapter, _ = self.downloader([(200,b'abc',{'Content-Type':'image/jpeg','Content-Length':'3'})])
        worker.download(self.task())
        self.assertEqual(worker.download(self.task(category='purchased')),('existing',0))
        self.assertEqual(len(adapter.requests),1)

    def test_manifest_changed_size_requires_redownload(self):
        worker, _, _ = self.downloader([(200,b'abc',{'Content-Type':'image/jpeg','Content-Length':'3'}),
                                        (200,b'abcdef',{'Content-Type':'image/jpeg','Content-Length':'6'})])
        worker.download(self.task())
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        path.write_bytes(b'broken-longer')
        self.assertEqual(worker.download(self.task()),('downloaded',6))
        self.assertEqual(path.read_bytes(),b'abcdef')

    def part(self, task=None, validator='"v1"'):
        task = task or self.task()
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        path.parent.mkdir(parents=True,exist_ok=True)
        Path(str(path)+'.part').write_bytes(b'abc')
        Path(str(path)+'.part.json').write_text(json.dumps({'identity':task.identity, 'validator':validator, 'total':6}))
        return path

    def test_valid_range_resumes_partial_file(self):
        path = self.part()
        worker, adapter, _ = self.downloader([(206,b'def',{'Content-Type':'image/jpeg','Content-Length':'3','Content-Range':'bytes 3-5/6','ETag':'"v1"'})])
        self.assertEqual(worker.download(self.task()),('downloaded',3))
        self.assertEqual(path.read_bytes(),b'abcdef')
        self.assertEqual(adapter.requests[0].headers['Range'],'bytes=3-')
        self.assertEqual(adapter.requests[0].headers['If-Range'],'"v1"')

    def test_server_ignoring_range_restarts_safely(self):
        path = self.part()
        worker, _, _ = self.downloader([(200,b'ABCDEF',{'Content-Type':'image/jpeg','Content-Length':'6','ETag':'"v2"'})])
        worker.download(self.task())
        self.assertEqual(path.read_bytes(),b'ABCDEF')

    def test_partial_without_validator_is_not_blindly_appended(self):
        path = self.part(validator='')
        worker, adapter, _ = self.downloader([(200,b'ABCDEF',{'Content-Type':'image/jpeg','Content-Length':'6'})])
        worker.download(self.task())
        self.assertNotIn('Range',adapter.requests[0].headers)
        self.assertEqual(path.read_bytes(),b'ABCDEF')

    def test_bad_range_never_produces_complete_file(self):
        path = self.part()
        worker, _, _ = self.downloader([(206,b'def',{'Content-Type':'image/jpeg','Content-Length':'3','Content-Range':'bytes 2-4/6','ETag':'"v1"'})])
        with self.assertRaises(AppError):
            worker.download(self.task())
        self.assertFalse(path.exists())

    def test_truncated_stream_never_counted_complete(self):
        worker, _, manifest = self.downloader([(200,b'abc',{'Content-Type':'image/jpeg','Content-Length':'6','ETag':'"v1"'})])
        with self.assertRaises(AppError):
            worker.download(self.task())
        self.assertIsNone(manifest.find(self.task()))
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        self.assertFalse(path.exists())
        self.assertEqual(Path(str(path)+'.part').read_bytes(),b'abc')

    def test_html_success_response_is_not_saved_as_media(self):
        worker, _, _ = self.downloader([(200,b'<html>Log in</html>',{'Content-Type':'text/html'})])
        with self.assertRaises(AppError):
            worker.download(self.task())
        self.assertFalse((self.root/'creator/photos/2026-10-01_123.jpg').exists())

    def test_empty_response_not_saved(self):
        worker, _, _ = self.downloader([(200,b'',{'Content-Type':'image/jpeg','Content-Length':'0'})])
        with self.assertRaises(AppError):
            worker.download(self.task())

    def test_external_redirect_rejected(self):
        worker, adapter, _ = self.downloader([(302,b'',{'Location':'https://evil.example/file.jpg'})])
        with self.assertRaises(AppError):
            worker.download(self.task())
        self.assertEqual(len(adapter.requests),1)

    def test_allowed_cdn_redirect_has_no_account_credentials(self):
        worker, adapter, _ = self.downloader([(302,b'',{'Location':'https://cdn3.onlyfans.com/file.jpg'}),
                                              (200,b'abc',{'Content-Type':'image/jpeg','Content-Length':'3'})])
        worker.download(self.task())
        self.assertEqual(len(adapter.requests),2)
        self.assertNotIn('Cookie',adapter.requests[1].headers)
        self.assertNotIn('x-bc',adapter.requests[1].headers)

    def test_url_allowlist_blocks_deceptive_hosts_and_http(self):
        for value in ['http://cdn2.onlyfans.com/a.jpg','https://onlyfans.com.evil.example/a.jpg',
                      'https://onlyfans.com@evil.example/a.jpg','https://localhost/a.jpg',
                      'https://onlyfans.com:8443/a.jpg']:
            with self.subTest(value=value), self.assertRaises(AppError):
                validate_media_url(value)

    def test_profile_parsing_accepts_urls_and_deduplicates(self):
        self.assertEqual(parse_profiles('@Alice, https://onlyfans.com/bob\nALICE'),['alice','bob'])

    def test_profile_parsing_rejects_paths_or_wrong_hosts(self):
        for text in ['../bad','https://evil.example/alice','https://onlyfans.com/alice/posts','..']:
            with self.subTest(text=text), self.assertRaises(AppError):
                parse_profiles(text)

    def test_windows_reserved_name_safe(self):
        self.assertNotEqual(safe_component('CON').upper(),'CON')
        self.assertNotIn('/',safe_component('../../bad'))

    def test_symlink_escape_is_rejected(self):
        outside = self.root/'outside'
        outside.mkdir()
        download_root = self.root/'downloads'
        download_root.mkdir()
        try:
            (download_root/'creator').symlink_to(outside,target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest('Symlink creation is not permitted')
        manifest = Manifest(download_root)
        self.addCleanup(manifest.close)
        worker = Downloader(Options(output_dir=download_root,profiles=['creator'],albums=False),'UA',Control(),manifest)
        self.addCleanup(worker.close)
        with self.assertRaises(AppError):
            worker.download(self.task())

    def post(self, **changes):
        post = {'id':10,'postedAt':'2026-10-01T23:00:00-02:00','canViewMedia':True,
                'media':[{'id':123,'type':'photo','canView':True,'files':{'full':{'url':'https://cdn2.onlyfans.com/a.jpg'}}}]}
        post.update(changes)
        return post

    def test_media_date_filter_normalizes_timezone(self):
        post = self.post()
        options = Options(output_dir=self.root,profiles=['creator'],since=datetime(2026,10,2,tzinfo=timezone.utc))
        task, reason = media_task(post,post['media'][0],'42','creator','posts',options)
        self.assertIsNotNone(task)
        self.assertEqual(task.date,'2026-10-02')

    def test_explicitly_locked_media_is_skipped(self):
        post = self.post()
        post['media'][0]['canView'] = False
        task, reason = media_task(post,post['media'][0],'42','creator','posts',self.options)
        self.assertIsNone(task)
        self.assertEqual(reason,'unavailable')

    def test_drm_media_is_skipped(self):
        post = self.post()
        post['media'][0]['files']['drm'] = {'manifest':'https://cdn2.onlyfans.com/a.mpd'}
        task, reason = media_task(post,post['media'][0],'42','creator','posts',self.options)
        self.assertIsNone(task)
        self.assertEqual(reason,'unavailable')

    def test_preview_is_opt_in_and_distinct_from_full(self):
        post = self.post()
        post['media'][0]['files'] = {'full':{'url':None},'preview':{'url':'https://cdn2.onlyfans.com/preview.jpg'}}
        task, _ = media_task(post,post['media'][0],'42','creator','posts',self.options)
        self.assertIsNone(task)
        self.options.previews = True
        task, _ = media_task(post,post['media'][0],'42','creator','posts',self.options)
        self.assertTrue(task.preview)
        self.assertIn('_preview',str(task.relative_path(self.options)))

    def test_gif_respects_photo_toggle(self):
        post = self.post()
        post['media'][0]['type'] = 'gif'
        self.options.photos = False
        task, reason = media_task(post,post['media'][0],'42','creator','posts',self.options)
        self.assertIsNone(task)
        self.assertEqual(reason,'filtered')

    def test_messages_and_purchases_respect_date_filter(self):
        post = self.post(createdAt='2020-01-01T00:00:00Z',postedAt=None)
        self.options.since = datetime(2026,1,1,tzinfo=timezone.utc)
        for category in ['messages','purchased']:
            task, reason = media_task(post,post['media'][0],'42','creator',category,self.options)
            self.assertIsNone(task)
            self.assertEqual(reason,'filtered')

    def test_dry_run_does_not_create_output_directory(self):
        outer = self
        class Client:
            auth = Credentials('123','UA','bc','session')
            def user(self,name):
                return {'id':42,'username':name}
            def paginate(self,endpoint,kind,**kwargs):
                return iter([outer.post()] if kind == 'posts' else [])
        root = self.root/'not-created'
        options = Options(output_dir=root,profiles=['creator'],categories=('posts',),dry_run=True)
        summary = run_downloads(Client(),options,Control())
        self.assertEqual(summary.planned,1)
        self.assertEqual(summary.downloaded,0)
        self.assertFalse(root.exists())

    def test_purchases_fetched_once_for_multiple_profiles(self):
        class Client:
            auth = Credentials('123','UA','bc','session')
            calls = []
            def user(self,name):
                return {'id':{'creator':42,'second':43}.get(name,123),'username':name}
            def paginate(self,endpoint,kind,**kwargs):
                self.calls.append((endpoint,kind))
                return iter([])
        client = Client()
        options = Options(output_dir=self.root,profiles=['creator','second'],categories=('purchased',),dry_run=True)
        run_downloads(client,options,Control())
        self.assertEqual(sum(kind=='purchased' for _,kind in client.calls),1)

    def test_concurrent_output_lock_is_rejected(self):
        with FolderLock(self.root):
            with self.assertRaises(AppError):
                with FolderLock(self.root):
                    pass

    def test_legacy_original_subfolder_layout_is_found_when_current_layout_is_flat(self):
        # Original script: non-post categories were optionally rooted under category/.
        legacy = self.root/'creator/messages/photos/2026-10-01_123.jpg'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'old-copy')
        options = Options(output_dir=self.root, profiles=['creator'], albums=False, subfolders=False)
        worker, adapter, manifest = self.downloader([], options=options)
        self.assertEqual(worker.download(self.task(category='messages')), ('existing', 0))
        self.assertFalse(adapter.requests)
        self.assertEqual(manifest.find(self.task(category='messages')), legacy)

    def test_legacy_original_album_layout_is_found_when_albums_now_disabled(self):
        # Original script placed multi-photo posts in photos/<date>_<post-id>/.
        legacy = self.root/'creator/photos/2026-10-01_999/2026-10-01_123.jpg'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'old-album-copy')
        options = Options(output_dir=self.root, profiles=['creator'], albums=False)
        worker, adapter, manifest = self.downloader([], options=options)
        task = self.task(album='999')
        self.assertEqual(worker.download(task), ('existing', 0))
        self.assertFalse(adapter.requests)
        self.assertEqual(manifest.find(task), legacy)

    def test_legacy_original_gif_folder_is_recognized(self):
        # The supplied original uses media["type"] + "s", so GIFs lived in /gifs/.
        legacy = self.root/'creator/gifs/2026-10-01_123.gif'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'GIF89a-old')
        worker, adapter, manifest = self.downloader([])
        task = self.task(kind='gif', source='https://cdn2.onlyfans.com/files/123.gif')
        self.assertEqual(worker.download(task), ('existing', 0))
        self.assertFalse(adapter.requests)
        self.assertEqual(manifest.find(task), legacy)

    def test_legacy_original_gif_folder_matches_image_extension_variants(self):
        legacy = self.root/'creator/gifs/2026-10-01_123.webp'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'old-gif-webp')
        worker, adapter, manifest = self.downloader([])
        task = self.task(kind='gif', source='https://cdn2.onlyfans.com/files/123.gif')
        self.assertEqual(worker.download(task), ('existing', 0))
        self.assertFalse(adapter.requests)
        self.assertEqual(manifest.find(task), legacy)

    def test_legacy_lookup_ignores_empty_and_part_files(self):
        folder = self.root/'creator/messages/photos'
        folder.mkdir(parents=True)
        (folder/'2026-10-01_123.jpg').touch()
        (folder/'2026-10-01_123.jpg.part').write_bytes(b'partial')
        options = Options(output_dir=self.root, profiles=['creator'], albums=False, subfolders=False)
        worker, adapter, _ = self.downloader([(200,b'new',{'Content-Type':'image/jpeg','Content-Length':'3'})], options=options)
        self.assertEqual(worker.download(self.task(category='messages')), ('downloaded', 3))
        self.assertEqual(len(adapter.requests), 1)

    def test_completed_download_emits_media_complete_event(self):
        events = []
        session, _ = session_for([(200,b'abcdef',{'Content-Type':'image/jpeg','Content-Length':'6'})])
        manifest = Manifest(self.root)
        self.addCleanup(manifest.close)
        worker = Downloader(self.options,'UA',Control(),manifest,session=session,retries=0,emit=lambda kind,value: events.append((kind,value)))
        self.addCleanup(worker.close)
        worker.download(self.task())
        completions = [value for kind,value in events if kind == 'media_complete']
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]['kind'], 'photo')
        self.assertEqual(completions[0]['profile'], 'creator')
        self.assertEqual(Path(completions[0]['path']), self.root/'creator/photos/2026-10-01_123.jpg')
        self.assertEqual(completions[0]['size'], 6)

    def test_existing_legacy_files_do_not_emit_preview_events(self):
        path = self.root/'creator/photos/2026-10-01_123.jpg'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous')
        events = []
        session, _ = session_for([])
        manifest = Manifest(self.root)
        self.addCleanup(manifest.close)
        worker = Downloader(self.options,'UA',Control(),manifest,session=session,retries=0,emit=lambda kind,value: events.append((kind,value)))
        self.addCleanup(worker.close)
        worker.download(self.task())
        self.assertFalse([value for kind,value in events if kind == 'media_complete'])


if __name__ == '__main__':
    unittest.main()
