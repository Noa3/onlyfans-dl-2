import io
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter

from ofdl.common import AppError, Control
from ofdl.live import LiveOptions, record_live
from test_api import session_for

URL = 'https://cdn2.onlyfans.com/live/index.m3u8?token=private-token'
TS = bytes([0x47]) + b'\0' * 187


def playlist(sequence, names, *, end=False, extra=''):
    text = f'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:{sequence}\n{extra}'
    text += ''.join(f'#EXTINF:4,\n{name}\n' for name in names)
    return (text + ('#EXT-X-ENDLIST\n' if end else '')).encode()


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'recordings'
        self.options = LiveOptions(URL,self.root)
        self.control = Control()
        self.events = []

    def run_recording(self, replies, emit=None):
        session, adapter = session_for([(200,data,{}) if isinstance(data,bytes) else data for data in replies])
        self.adapter = adapter
        with patch.object(self.control,'wait',side_effect=lambda seconds:self.control.checkpoint()):
            return record_live(self.options,self.control,emit or (lambda k,v:self.events.append((k,v))),session=session)

    def test_sliding_playlist_records_each_sequence_once(self):
        result = self.run_recording([playlist(10,['10.ts','11.ts']),TS,TS,
                                     playlist(11,['11.ts','12.ts'],end=True),TS])
        self.assertEqual((result.segments,result.media_seconds,result.reason), (3,12,'ended'))
        self.assertEqual(result.path.read_bytes(), TS*3)
        self.assertEqual(result.path.suffix, '.ts')
        self.assertEqual(len(self.adapter.requests), 5)
        self.assertFalse(list(self.root.glob('*.part')))
        self.assertNotIn('private-token', str(self.events))

    def test_stop_saves_only_complete_segments(self):
        def emit(kind, value):
            if kind == 'live_progress':
                self.control.stop()
        result = self.run_recording([playlist(1,['1.ts','2.ts']),TS],emit)
        self.assertEqual(result.reason,'stopped')
        self.assertEqual(result.path.read_bytes(),TS)
        self.assertEqual(len(self.adapter.requests),2)

    def test_encryption_is_rejected_before_requesting_keys_or_segments(self):
        for tag in ('#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n',
                    '#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="key.bin"\n'):
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(AppError,'Encrypted'):
                    self.run_recording([playlist(1,['1.ts'],extra=tag)])
                self.assertEqual(len(self.adapter.requests),1)
                self.assertFalse(self.root.exists())

    def test_external_redirects_and_segment_hosts_are_rejected(self):
        for replies in [[(302,b'',{'Location':'https://evil.example/steal'})],
                        [playlist(1,['https://evil.example/segment.ts'],end=True)]]:
            with self.subTest(replies=replies):
                with self.assertRaisesRegex(AppError,'OnlyFans'):
                    self.run_recording(replies)
                self.assertEqual(len(self.adapter.requests),1)

    def test_failure_retains_partial_file_and_redacts_url(self):
        with self.assertRaises(AppError) as error:
            self.run_recording([playlist(1,['1.ts','2.ts']),TS,(403,b'private-token',{})])
        self.assertNotIn('private-token',str(error.exception))
        self.assertEqual(next(self.root.glob('*.part')).read_bytes(),TS)
        self.assertFalse(list(self.root.glob('*.ts')))

    def test_gap_is_reported_instead_of_silently_skipping_segments(self):
        with self.assertRaisesRegex(AppError,'missed'):
            self.run_recording([playlist(1,['1.ts']),TS,playlist(3,['3.ts'],end=True)])
        self.assertEqual(next(self.root.glob('*.part')).read_bytes(),TS)

    def test_fragmented_mp4_writes_initialization_once(self):
        init = b'\0\0\0\x0cftypdata'
        fragment = b'\0\0\0\x0cmoofdata'
        result = self.run_recording([playlist(1,['1.m4s','2.m4s'],end=True,
                                              extra='#EXT-X-MAP:URI="init.mp4"\n'),init,fragment,fragment])
        self.assertEqual(result.path.suffix,'.mp4')
        self.assertEqual(result.path.read_bytes(),init+fragment*2)

    def test_master_selects_highest_muxed_variant(self):
        master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=200\nhigh.m3u8\n'
        result = self.run_recording([master,playlist(1,['1.ts'],end=True),TS])
        self.assertTrue(self.adapter.requests[1].url.endswith('/high.m3u8'))
        self.assertEqual(result.segments,1)

    def test_separate_audio_and_byte_ranges_are_explicitly_unsupported(self):
        master = b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="audio.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=200,AUDIO="a"\nvideo.m3u8\n'
        with self.assertRaisesRegex(AppError,'separate audio'):
            self.run_recording([master])
        with self.assertRaisesRegex(AppError,'Byte-range'):
            self.run_recording([playlist(1,['1.ts'],extra='#EXT-X-BYTERANGE:188@0\n')])

    def test_non_media_segment_is_not_saved(self):
        with self.assertRaisesRegex(AppError,'non-media'):
            self.run_recording([playlist(1,['1.ts'],end=True),b'<html>login</html>'])
        self.assertFalse(self.root.exists())

    def test_encryption_appearing_on_refresh_keeps_clear_partial_segments(self):
        with self.assertRaisesRegex(AppError,'Encrypted'):
            self.run_recording([playlist(1,['1.ts']),TS,
                                playlist(2,['2.ts'],extra='#EXT-X-KEY:METHOD=AES-128,URI="key"\n')])
        self.assertEqual(next(self.root.glob('*.part')).read_bytes(),TS)
        self.assertEqual(len(self.adapter.requests),3)

    def test_live_stall_terminates_and_keeps_partial_recording(self):
        times = iter([0,0,121])  # start, last segment, stall check
        with patch('ofdl.live.time.monotonic',side_effect=lambda:next(times)), \
                self.assertRaisesRegex(AppError,'two minutes'):
            self.run_recording([playlist(1,['1.ts']),TS,playlist(1,['1.ts'])])
        self.assertEqual(next(self.root.glob('*.part')).read_bytes(),TS)

    def test_duration_limit_finalizes_complete_segments(self):
        self.options.max_seconds = 10
        times = iter([0,0,0,1,2,3,11])
        with patch('ofdl.live.time.monotonic',side_effect=lambda:next(times)):
            result = self.run_recording([playlist(1,['1.ts']),TS,playlist(1,['1.ts'])])
        self.assertEqual(result.reason,'duration limit')
        self.assertEqual(result.path.read_bytes(),TS)

    def test_proxy_used_and_account_credentials_not_forwarded(self):
        self.options.proxy = 'http://127.0.0.1:8888'
        session, adapter = session_for([(200,playlist(1,['1.ts'],end=True),{}),(200,TS,{})])
        session.headers['Cookie'] = 'sess=never-forward'
        session.headers['x-bc'] = 'never-forward'
        record_live(self.options,self.control,session=session)
        for request, options in zip(adapter.requests,adapter.options):
            self.assertNotIn('Cookie',request.headers)
            self.assertNotIn('x-bc',request.headers)
            self.assertEqual(options['proxies']['https'], self.options.proxy)
            self.assertTrue(options['verify'])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Optional FFmpeg integration fixture')
    def test_real_ts_and_fragmented_mp4_recordings_are_playable(self):
        for kind in ('mpegts','fmp4'):
            with self.subTest(container=kind):
                source = Path(self.temp.name)/kind
                source.mkdir()
                command = [shutil.which('ffmpeg'),'-hide_banner','-loglevel','error',
                           '-f','lavfi','-i','testsrc=size=160x90:rate=10',
                           '-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
                           '-t','3','-c:v','libx264','-pix_fmt','yuv420p','-g','10','-sc_threshold','0',
                           '-c:a','aac','-hls_time','1','-hls_playlist_type','vod',
                           '-hls_segment_type',kind,str(source/'index.m3u8')]
                subprocess.run(command,check=True,capture_output=True,timeout=30,cwd=source)
                class Files(BaseAdapter):
                    def send(self, request, **kwargs):
                        response = requests.Response()
                        response.status_code = 200
                        response.raw = io.BytesIO((source/Path(urlsplit(request.url).path).name).read_bytes())
                        response.url = request.url
                        return response
                    def close(self):
                        pass
                session = requests.Session()
                session.mount('https://',Files())
                result = record_live(self.options,self.control,session=session)
                probe = subprocess.run([shutil.which('ffprobe'),'-v','error','-show_format','-show_streams',
                                        '-of','json',str(result.path)],check=True,capture_output=True,timeout=20)
                metadata = json.loads(probe.stdout)
                self.assertEqual({item['codec_type'] for item in metadata['streams']},{'video','audio'})
                self.assertGreater(float(metadata['format']['duration']),2.5)
                subprocess.run([shutil.which('ffmpeg'),'-v','error','-i',str(result.path),'-f','null','-'],
                               check=True,capture_output=True,timeout=20)
