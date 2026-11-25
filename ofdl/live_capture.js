/* WebRTC MediaRecorder bridge. Audio-tap approach adapted from the MIT-licensed
 * OF-Scraper-GUI Live Stream Monitor; see THIRD_PARTY_NOTICES.md. */
(() => {
    const taps = new Map();
    const playingAudio = new Set();
    const connect = AudioNode.prototype.connect;
    AudioNode.prototype.connect = function(destination, ...args) {
        if (destination instanceof AudioDestinationNode) {
            try {
                let tap = taps.get(this.context);
                if (!tap && taps.size < 16) {
                    tap = this.context.createMediaStreamDestination();
                    taps.set(this.context, tap);
                }
                if (tap) connect.call(this, tap, ...args);
            } catch (_) { /* Capture must not break the player's audio graph. */ }
        }
        return connect.call(this, destination, ...args);
    };
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function(...args) {
        if (this.srcObject instanceof MediaStream && playingAudio.size < 64) {
            playingAudio.add(this);
        }
        return play.apply(this, args);
    };

    let recorder, video, sourceTrack;
    let pending = Promise.resolve();
    let chunks = [], queuedBytes = 0, error = '', audio = false;
    let startedAt = 0;
    const limit = 16 * 1024 * 1024;
    const live = track => track.readyState === 'live' && track.enabled;
    function start(allowSilent) {
        if (recorder) return {ok:true, audio};
        video = [...document.querySelectorAll('video')].find(element =>
            !element.mediaKeys && element.readyState >= 2 &&
            element.srcObject instanceof MediaStream &&
            element.srcObject.getVideoTracks().some(live));
        if (!video) return {ok:false};
        sourceTrack = video.srcObject.getVideoTracks().find(live);
        let audioTrack = video.srcObject.getAudioTracks().find(live);
        if (!audioTrack) {
            for (const element of [...document.querySelectorAll('audio'), ...playingAudio]) {
                if (element.srcObject instanceof MediaStream) {
                    audioTrack = element.srcObject.getAudioTracks().find(live);
                    if (audioTrack) break;
                }
            }
        }
        if (!audioTrack) {
            for (const tap of taps.values()) {
                audioTrack = tap.stream.getAudioTracks().find(live);
                if (audioTrack) break;
            }
        }
        if (!audioTrack && !allowSilent) return {ok:false, waitingAudio:true};
        const mimeType = ['video/webm;codecs=vp8,opus', 'video/webm;codecs=vp9,opus',
                          'video/webm'].find(type => MediaRecorder.isTypeSupported(type));
        if (!mimeType) throw new Error('No WebM encoder');
        audio = !!audioTrack;
        recorder = new MediaRecorder(new MediaStream(
            audioTrack ? [sourceTrack, audioTrack] : [sourceTrack]), {mimeType});
        recorder.ondataavailable = event => {
            if (!event.data.size) return;
            queuedBytes += event.data.size;
            if (queuedBytes > limit) {
                error = 'Capture buffer full';
                if (recorder.state !== 'inactive') recorder.stop();
                return;
            }
            // Serialize conversion: asynchronous blobs must never reorder WebM chunks.
            pending = pending.then(async () => {
                const bytes = new Uint8Array(await event.data.arrayBuffer());
                let binary = '';
                for (let offset = 0; offset < bytes.length; offset += 8192) {
                    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
                }
                chunks.push({data:btoa(binary), size:bytes.length});
            }).catch(() => { error = 'Chunk conversion failed'; });
        };
        recorder.onerror = () => { error = 'MediaRecorder failed'; };
        recorder.start(1000);
        startedAt = performance.now();
        return {ok:true, audio};
    }
    function drain() {
        const result = chunks;
        chunks = [];
        queuedBytes -= result.reduce((total, item) => total + item.size, 0);
        return {chunks:result.map(item => item.data), error, audio,
                ended:sourceTrack?.readyState === 'ended',
                muted:!!sourceTrack?.muted,
                mediaTime:video?.currentTime || 0,
                elapsed:startedAt ? (performance.now() - startedAt) / 1000 : 0};
    }
    async function stop() {
        const finish = async () => {
            if (recorder && recorder.state !== 'inactive') {
                await new Promise(resolve => {
                    recorder.addEventListener('stop', resolve, {once:true});
                    recorder.stop();
                });
            }
            await pending;
            return true;
        };
        const done = await Promise.race([finish(), new Promise(resolve => setTimeout(() => resolve(false), 10000))]);
        if (!done) error = 'Final media flush timed out';
        return drain();
    }
    window.ofdlCapture = {start, drain, stop};
})();
