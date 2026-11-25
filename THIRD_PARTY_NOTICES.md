# Third-party references and notices

## OF-Scraper-GUI / OF-Scraper Live Stream Monitor

Reference: <https://github.com/cjb900/OF-Scraper-GUI/tree/main/OF-Scraper-3.14.7/ofscraper/plugins/live_stream_monitor>

The automatic live recorder uses the subscription `hasStream` discovery and
`/users/{username}/streams/active?view=full_with_check` observations from this
project. The Web Audio destination tap and detached audio-element tracking in
`ofdl/live_capture.js` are adapted from its MediaRecorder approach. Scheduling,
Tk integration, request restrictions, bounded ordered chunk transport and output
lifecycle were implemented for this application. No native Agora SDK is bundled.
These observations are not an official platform contract or a real-account test
of this application.

License from `OF-Scraper-3.14.7/LICENSE`:

MIT License

Copyright (c) 2023 datawhores

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
