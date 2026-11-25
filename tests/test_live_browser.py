import base64
import importlib.util
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ofdl.common import AppError, Control
from ofdl.live_browser import (BrowserOutput, CAPTURE_SCRIPT, allowed_browser_request,
                               capture_page, live_page, record_browser)
from ofdl.live_watch import LiveStream, WatchOptions
from test_api import AUTH


class BrowserRecordingTests(unittest.TestCase):
    def test_account_mutations_are_blocked_except_exact_view_heartbeat(self):
        base = 'https://onlyfans.com/api2/v2/'
        for endpoint in ['subscriptions/1','payments/pay','tips','streams/42/join',
                         'streams/43/look','streams/42/look?price=0','streams/42/look/']:
            self.assertFalse(allowed_browser_request(base+endpoint,'POST','{}','42'))
        for body in ['{"price":1}','{"tip":10}',' ']:
            self.assertFalse(allowed_browser_request(base+'streams/42/look','POST',body,'42'))
        self.assertTrue(allowed_browser_request(base+'streams/42/look','POST','{}','42'))
        self.assertTrue(allowed_browser_request(base+'users/me','GET',None,'42'))
        self.assertFalse(allowed_browser_request('https://cdn2.onlyfans.com/api2/v2/streams/42/look','POST','{}','42'))
        self.assertTrue(allowed_browser_request('https://rtc.example/signal','POST','{}','42'))
        self.assertFalse(allowed_browser_request('http://onlyfans.com/api2/v2/users/me','GET',None,'42'))

    def test_login_redirect_or_wrong_host_is_not_live(self):
        self.assertTrue(live_page('https://onlyfans.com/alice/live','alice'))
        for url in ['https://evil.example/alice/live','https://onlyfans.com/?return_to=/alice/live',
                    'https://onlyfans.com/alice/live?return_to=x','https://onlyfans.com/bob/live']:
            self.assertFalse(live_page(url,'alice'))

    def test_ordered_output_and_failure_preserves_part(self):
        with TemporaryDirectory() as temp:
            output = BrowserOutput(Path(temp),'creator')
            chunk = b'\x1a\x45\xdf\xa3fixture'
            output.append({'chunks':[base64.b64encode(chunk).decode()], 'elapsed':1,'audio':True})
            with self.assertRaises(AppError):
                output.append({'chunks':[], 'error':'overflow'})
            output.close()
            self.assertEqual(output.part.read_bytes(),chunk)
            self.assertFalse(output.final.exists())
            result = output.finish('stopped')
            self.assertEqual(result.path.read_bytes(),chunk)
            self.assertFalse(output.part.exists())

    def test_invalid_header_does_not_create_output(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)/'out'
            output = BrowserOutput(directory,'creator')
            with self.assertRaises(AppError):
                output.append({'chunks':[base64.b64encode(b'not webm').decode()]})
            self.assertFalse(directory.exists())

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'Optional Playwright package')
    def test_browser_uses_temporary_context_restricts_requests_and_cleans_up(self):
        with TemporaryDirectory() as temp,patch('playwright.sync_api.sync_playwright') as factory, \
                patch('ofdl.live_browser.capture_page') as capture:
            browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
            context = browser.new_context.return_value
            page = context.new_page.return_value
            page.url = 'https://onlyfans.com/alice/live'
            page.locator.return_value.count.return_value = 1
            options = WatchOptions(Path(temp),proxy='http://127.0.0.1:8888')
            record_browser(LiveStream('alice','42'),AUTH,options,Control())
            cookies = context.add_cookies.call_args.args[0]
            self.assertTrue(all(cookie['url'] == 'https://onlyfans.com/' for cookie in cookies))
            self.assertNotIn('extra_http_headers',browser.new_context.call_args.kwargs)
            self.assertEqual(browser.new_context.call_args.kwargs['service_workers'],'block')
            self.assertTrue(context.route.called)
            self.assertTrue(capture.called)
            context.close.assert_called_once()
            browser.close.assert_called_once()

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'Optional Playwright package')
    def test_real_browser_capture_flushes_audio_video_and_decodes(self):
        from playwright.sync_api import sync_playwright, Error
        with TemporaryDirectory() as temp,sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True,args=['--autoplay-policy=no-user-gesture-required'])
            except Error:
                self.skipTest('Optional Chromium browser not installed')
            try:
                for audio_mode in ('direct','detached','web_audio'):
                    with self.subTest(audio_mode=audio_mode):
                        page = browser.new_page()
                        page.set_content('<canvas width="160" height="90"></canvas><video autoplay muted></video>')
                        page.evaluate(CAPTURE_SCRIPT)
                        page.evaluate('''mode => {
                            const canvas = document.querySelector('canvas');
                            const ctx = canvas.getContext('2d');
                            let n = 0;
                            setInterval(() => { ctx.fillStyle = (++n % 2) ? 'red' : 'blue';
                                                ctx.fillRect(0,0,160,90); }, 50);
                            const ac = new AudioContext();
                            const oscillator = ac.createOscillator();
                            const audio = ac.createMediaStreamDestination();
                            oscillator.connect(audio);
                            oscillator.start();
                            ac.resume();
                            const stream = canvas.captureStream(10);
                            if (mode === 'direct') stream.addTrack(audio.stream.getAudioTracks()[0]);
                            if (mode === 'detached') {
                                const element = new Audio();
                                element.srcObject = audio.stream;
                                element.play();
                                window.keepAudio = element;
                            }
                            if (mode === 'web_audio') oscillator.connect(ac.destination);
                            const video = document.querySelector('video');
                            video.srcObject = stream;
                            video.play();
                            window.keepContext = ac;
                        }''',audio_mode)
                        output = BrowserOutput(Path(temp),audio_mode)
                        try:
                            result = capture_page(page,output,Control(),max_seconds=2.5)
                        finally:
                            output.close()
                            page.close()
                        self.assertGreater(result.bytes_written,1000)
                        self.assertFalse(list(Path(temp).glob('*.part')))
                        if shutil.which('ffprobe') and shutil.which('ffmpeg'):
                            probe = subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(result.path)],
                                                   check=True,capture_output=True,timeout=20)
                            self.assertEqual({s['codec_type'] for s in json.loads(probe.stdout)['streams']},{'video','audio'})
                            subprocess.run(['ffmpeg','-v','error','-i',str(result.path),'-f','null','-'],
                                           check=True,capture_output=True,timeout=20)
            finally:
                browser.close()
