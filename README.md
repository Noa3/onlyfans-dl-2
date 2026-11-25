# OnlyFans DL — Desktop 2.8.0

<img width="706" height="597" alt="grafik" src="https://github.com/user-attachments/assets/c5273233-e35f-4e3e-864f-98f5b79bc7e3" /><img width="703" height="595" alt="grafik" src="https://github.com/user-attachments/assets/6e27fac5-858b-46a5-8734-364618beb937" />


This tool downloads all photos/videos from OnlyFans profiles, creating a local archive.
You must be subscribed to the profile to download their content.

A local desktop UI and CLI built on the supplied `onlyfans-dl.py`. The old
edit-the-constants workflow is gone: you set up a session in a form, pick creators
and filters, watch progress, and a separate, testable download engine does the work.

**Use your own account and only content you are entitled and permitted to save.**
This is an unofficial client. It does not bypass access controls, DRM, or security
challenges.

**Extract the entire folder.** Keep `onlyfans-dl.py`, the `ofdl/` package, and the
other files together. The launcher is not a standalone replacement file.

## What's new in 2.8.0

- **Free/trial claims are back, with hard guards.** Select zero-price rows and use
  **Subscribe / claim selected**; confirm the warning, and the tool subscribes free
  pages or claims shared trial codes. Only `$0` rows with a known claim path are sent.
  Free pages are re-read immediately before the POST and refused if they are no longer
  free or already subscribed. Trial codes are claimed by code; code-less trials stay
  website-only. POSTs are never retried.
- **Renewal warning.** A free trial normally auto-renews at the creator's regular
  monthly price unless cancelled on the website. The confirmation dialog states this
  explicitly. A price change between the final check and the server's decision cannot
  be fully excluded, so every batch requires confirmation.
- **More free/trial discovery.** Expired creators' newest chat messages are now
  searched for fresh trial links. A **Deep scan** option raises the bounded chat-page,
  chat-partner, list and list-user limits. A **Search** box filters loaded rows by
  creator, source or detail. New settings are remembered.
- **Safer bookkeeping.** Successful claims update the table and `free_trials.json`
  (trial codes stored only as hashes). Paid pages, paid promotions and purchases are
  still never listed or started.

## What's new in 2.7.0

- **Opt-in automatic live recording.** Enable once in Live recording; current
  subscriptions are checked every 60 seconds by default. Accessible live WebRTC
  players open in temporary browsers and record audio/video to WebM without copied
  playlist URLs. The setting is remembered, and resumes once a session is available.
- **Simple setup and controls.** One-click browser setup, optional creator include/
  skip lists, up to four parallel recordings (default two), per-stream status/file
  paths, individual stop, and stop-all with media flushing. Runs beside Downloads.
- **Bounded recovery.** Stream-ID deduplication within a monitoring session,
  failure cooldowns, three attempts per stream, API backoff and graceful shutdown.
  Auth failures pause monitoring until settings change or it is re-enabled.
- **Browser recording adapted from OF-Scraper-GUI Live Stream Monitor.** Supports
  direct, detached-element and Web Audio audio tracks. See
  [third-party notices](THIRD_PARTY_NOTICES.md). Real-account behavior is unverified;
  tests exercise simulated discovery and locally generated browser audio/video.

## What's new in 2.6.0

- **Download while discovering.** After creator selection is resolved, the first
  discovered media starts downloading while the API continues paging more content.
  A bounded queue holds at most 64 pending items; duplicate detection, resumable
  files and the manifest remain enabled. Scan-only still writes no media or manifest.
- **Honest growing-total progress.** During discovery, Activity shows processed
  files, files found so far and pending items. Final percentage/ETA appear after
  discovery completes. Pause/Stop apply to both discovery and downloads.
- **Live recording tab.** Record an accessible HTTPS OnlyFans HLS playlist URL in
  parallel with Downloads, with its own Start and Stop-and-save buttons, output
  folder, label and optional time limit. Supports clear muxed MPEG-TS and fragmented
  MP4; complete segments are saved on stop and partial recordings survive failures.
  No FFmpeg installation is needed for recording; install the updated requirements
  for the `m3u8` parser. See the live-recording instructions below for supported formats.

## What's new in 2.5.1

- **Account actions fail closed.** Automatic subscriptions and trial claims were
  disabled pending a verified zero-price contract. Scanned prices and a last-minute
  profile check cannot eliminate a price-change race. (Re-enabled for zero-price
  offers with a fresh check and explicit confirmation in 2.8.0.) Discovery, sorting
  and **Open profile / link** remain available for manual website review.
  Existing subscriptions and purchased content remain downloadable.
- **Duplicate keeper verification.** Both extra copies and the keeper are checked
  for content and file changes before deletion. A missing, changed or unsafe keeper
  stops cleanup for that group. Avoid concurrent library modifications during cleanup.
- **No write retries.** POST requests are sent at most once; ambiguous connection
  failures are reported without repeating an account action. GET retries remain enabled.
- **Partial scan results.** Rows appear as they are found and survive cancellation
  or later failures. Optional source errors mark the scan incomplete while other
  sources continue; authentication failures and rate limits still stop the scan.
- **Proxy privacy and threading.** Password-only proxy URLs are excluded from saved
  preferences. All network jobs snapshot the proxy on the main thread; editing it
  during a session test invalidates that result.
- **Accurate media counters.** `.mpd`/`.m3u8` URLs without explicit DRM flags count as
  unsupported streaming, separately from DRM-marked media, in the UI and CLI.
- **Separate tab views.** Free/trial and duplicate widgets live in `ui_offers.py`
  and `ui_duplicates.py`; worker coordination stays in `App`.

## What's new in 2.5.0

Historical release notes below describe the original behavior. Automatic account
actions introduced in 2.2.0–2.5.0 are disabled as of 2.5.1 (see above).

- **Broader free/trial scan.** The tab now also reads your **inbox chat partners**,
  your **custom lists** and the **website promotion banners** shown to your account, in
  addition to active/expired subscriptions, chat messages and names you type in.
  Creators who wrote to you or are in your lists are covered even without a
  subscription. Banners that contain a trial link become claimable rows; paid banners
  are skipped.
- **Visible scan progress.** An inline progress bar and a status line name the current
  phase (subscriptions, inbox, lists, promotions, chat messages, entered names) with
  done/total counters, and report start, stop, failure and finish.
- **No paid actions.** The tool never opens a paid subscription and never buys paid
  content. Only zero-price pages and shared free-trial codes can be acted on, after
  confirmation. Existing paid subscriptions and already purchased content keep
  downloading normally, including the Purchased category.
- Still no platform-wide scan: there is no endpoint that lists every creator, so
  coverage is limited to creators connected to your account.
- 220 local tests executed: 219 passed, 1 skipped.

## What's new in 2.4.0

- **Explicit proxy / VPN support.** A proxy field on the Session tab (and `--proxy` for
  the CLI) accepts `http://`, `https://`, `socks5://` and `socks5h://`. It is applied to
  the account API, the public rules download, the media downloads and the browser
  helper, because signed CDN URLs are tied to the requesting IP. Proxying only the API
  while media goes direct is the usual cause of media 403s.
- Ambient `*_PROXY` variables and `.netrc` stay ignored; only the explicit value is
  used. A proxy URL with username/password stays in memory and is never written to
  `settings.json`. SOCKS support comes from PySocks in `requirements.txt`.
- Help text now covers VPN pitfalls: Cloudflare 403 HTML on VPN ranges, rotating exit
  IPs breaking signed URLs, per-IP 429 limits, and split tunnels.
- **DRM items are counted separately** in the activity stats and CLI summary
  (`DRM-protected (skipped)`) instead of being folded into unavailable media. DRM is
  still never bypassed or decrypted.
- 214 local tests pass (208 retained plus proxy validation, proxy propagation,
  credential-safe persistence and DRM counting).

## What's new in 2.3.0

- **Duplicates tab.** Compares media files inside one library folder by SHA-256 and
  groups identical content, even when it sits under a different creator folder or an
  old layout. Only equal-size files are hashed, and hashes are cached per path, size and
  modification time in `file_hashes.sqlite3`.
- Each group marks one keeper: the copy the download manifest already knows, otherwise
  the shortest path. Select any file row and use **Use selected copy as keeper** to
  change it.
- **Delete other copies** removes only the non-kept files inside the chosen folder,
  after confirmation. Every extra copy is re-hashed right before deletion, so changed
  files are skipped; symbolic links are never followed. Deletion bypasses the recycle
  bin and cannot be undone.
- Symbolic links, hidden files, `.part` files and non-media files are ignored. The
  report is local only and uploads nothing.

## What's new in 2.2.0

- **Free & trials tab.** A new tab scans your active and expired subscriptions, the
  newest messages of those chats, and an optional creator list for offers that cost
  nothing. A page is free when its regular price is 0; a free trial is a 0 current
  price or a claimable 0 promotion on a paid page. Paid offers are never listed or
  acted on.
- **Sort and filter.** Filter by Free only / Trials only and Available / Already
  subscribed; click any column heading to sort.
- **Confirm-before-action subscribe.** Select rows and confirm; free pages use the
  account's own subscribe endpoint and shared trial links are claimed by their code.
  Rows for pages you already subscribe to are read-only and cannot be subscribed
  twice. Claimed codes are remembered (hashed, never plaintext) in
  `free_trials.json`, so a link is not claimed again.
- Trial offers without a code stay informational and open the website instead.
  Unsubscribing, renewals and paid promotion claims remain manual.

## What's new in 2.1.1

- **Cloudflare-compatible API transport.** Some networks answer the account API with a
  `403` HTML protection page even when the session is valid. API requests now carry the
  same optional browser headers a normal XHR sends, and when the optional `curl_cffi`
  package is installed they use a Chrome TLS impersonation profile. This is a
  compatibility transport, not a challenge solver — a real JavaScript/managed challenge
  is still never bypassed. The client falls back to plain `requests` if `curl_cffi` is
  missing. Rule downloads still use plain `requests`.
- Clearer guidance in the `403` HTML message: the session is often tied to the network
  it was created on, so capture and use it from the same connection.

## What's new in 2.1.0

- **Dark mode.** A **Dark mode / Light mode** button in the top-right header recolors the
  whole window instantly and is remembered in your saved preferences.
- **Automatic session load.** A checkbox on the Session tab loads your saved session from
  the OS credential store when the app starts. It stays quiet if nothing is saved and
  never writes a plaintext session file.
- **Scheduled new-content checks.** On Downloads, **Check for new content automatically**
  re-runs a download every 1–10080 minutes while the window stays open. It never starts
  the app for you and skips silently when setup or the session is not ready.
- **Overall progress with ETA.** A third progress bar shows discovery/paging, then the
  download-queue percentage with an estimated time to finish, above the existing per-run
  and per-file bars.
- **Roomier Activity preview.** The log is compacted and the Latest media preview area now
  expands to fill the window, with the **Open file** button pinned so it is always visible.
- The old "A local interface for your own account…" header subtitle was removed.

## What's new in 2.0.3

- **Activity preview.** The Activity tab now has a right-side **Latest media** panel
  showing creator/type, filename, size, preview, and **Open file**. Newly completed
  images/GIFs get a small (max 320×240) PNG thumbnail built off the UI thread. Videos
  get one optional poster frame only when `ffmpeg` is already on PATH; otherwise a
  lightweight placeholder plus **Open file**. No video playback loop and no bundled
  ffmpeg. Only the newest pending preview is kept, so a fast queue never builds a
  thumbnail backlog. Pillow is the only new normal dependency.
- **Large-library legacy adoption.** Existing files from the original script are
  recognized across the old flat, category-subfolder, photo-album, and `gifs/` layouts
  without reading file contents. Exact paths are checked first; only on a miss does the
  app lazily scan that creator's tree once per run and index by media ID/type. Found
  files are adopted into the SQLite manifest and counted as **Existing**. Built for
  libraries on the order of ~80,000 files / ~125 GB — no migration-time full hashing,
  and legacy manifest writes are batched. See [CHANGELOG.md](CHANGELOG.md).

Legacy files are trusted by filename/media ID and non-zero size on first adoption;
that cannot prove an old file is complete or uncorrupted. Delete a known-bad old file
rather than relying on duplicate detection to repair it. Do not run two application
versions against the same output folder at the same time.

2.0.2 (simpler creator selection and date fields) and 2.0.1 (authentication/signing
fixes) are retained. Your existing output folder and `.ofdl.sqlite3` manifest can be
reused.

## Quick start

**Windows** — Python 3.10+ with Tcl/Tk is required (source and launch scripts, not a
prebuilt `.exe`).

1. Extract into a normal writable folder, not inside another application.
2. Run `setup_windows.bat` once — creates a local `.venv` and installs dependencies.
3. Run `start_windows.bat` to open the app.

For the optional **Browser login** button, also run `install_browser_windows.bat`
(installs Playwright and downloads Chromium). **Paste copied request** works without it.

**Linux / macOS** — from the extracted folder, with Python 3.10+ and Tk installed:

```bash
bash setup.sh
bash start.sh
bash install_browser.sh   # optional visible-browser helper
```

`python3 -m tkinter` checks for working Tk. On Debian/Ubuntu the usual packages are
`python3-venv` and `python3-tk`. On Arch/CachyOS install `tk`
(`sudo pacman -S --needed tk`); the `python` package already includes `venv` and
`ensurepip`. Playwright on Linux may also need browser system
libraries; `.venv/bin/python -m playwright install-deps chromium` installs them on
supported distributions (and may request privileges) — the supplied installer does not
run that automatically.

The launch scripts do not request administrator privileges. Their Windows execution was
not tested in the Linux build environment.

## Session setup

### Method A — Browser login (optional helper)

Install the browser helper, then click **Browser login** on the Session tab. Pick
Chromium or an installed Chrome/Edge. A new visible, isolated browser opens; log in and
complete any verification yourself. Once a completed `/api2/v2/users/me` GET returns JSON
identifying the same account, the helper captures the four values, closes its browser, and
fills the form. Reload the home feed if no account response appears. Click **Test
session** before downloading.

The helper reads request headers and the account ID in the own-account response. It does
not read password/2FA POST bodies, attach to your normal browser profile, read saved
passwords, or write a persistent browser profile, tracing output, or storage-state
export. Temporary data may still exist in process memory or OS temp storage while it runs.
It does not hide automation or solve/bypass challenges — use Method B if login fails.

### Method B — Paste one copied request (no extra install)

1. In your normal browser, log into your account. Open DevTools (F12) → **Network**,
   reload, and filter for `/api2/v2/`.
2. Select a successful logged-in API request, right-click, **Copy as cURL** (bash form
   preferred).
3. In the app choose **Paste copied request**, paste/import, then **Test session**.

The importer extracts `USER_ID`, `USER_AGENT`, `X_BC`, and `SESS_COOKIE` together, and
preserves additional cookies in that request. Your numeric ID can be recovered from the
`auth_id` cookie when the `user-id` header is absent. **The pasted command is parsed as
text, never executed.** Raw headers, session JSON, and JSON/HAR files are also supported;
sanitized HAR exports may omit cookies and cannot supply a complete session.

Manual entry remains available. Use only the `sess` cookie's value in `SESS_COOKIE`, not
the whole Cookie header. `USER_ID` is your account ID, not the target creator's. Keep all
values from the same browser/account/session.

**Never share copied requests, HAR/session files, or session values in chat, issues, or
support tickets.** Treat them like passwords. The import dialog can clear the current
clipboard on request; it cannot erase clipboard managers or synced history.

## Choosing and running downloads

On **Downloads**, pick one explicit creator mode:

- **All active subscriptions (automatic)** — no list needed. Each Start/Scan fetches the
  current active subscriptions and includes all of them except names in Skip creators.
  Automatic scope means *active subscriptions*, not every creator on the platform or every
  account you once purchased from. For accessible purchases from a creator you no longer
  subscribe to, use the explicit mode.
- **Only these creators (optional)** — enables the text box. Enter usernames, `@names`,
  or HTTPS profile URLs separated by commas, spaces, or new lines. Skip creators still
  wins. A blank list, or one entirely skipped, is an error — never a fallback to everyone.

Then choose the output folder, media types, and content categories: photos/GIFs, video,
audio, and the posts, archived posts, stories, messages, and purchased categories. What
actually works depends on current API responses and your account's access.

**Scan only** discovers candidates without downloading or writing to the output folder
(it still contacts the account/API and may update the nonsecret rules cache). **Start
download** resolves creator selection, then scans and transfers files concurrently
through a deduplicated, bounded queue. Already completed files remain on disk even
if a later discovery request fails.

**Check for new content automatically** re-runs a download every 1–10080 minutes, but only
while this window stays open — it never registers an OS autostart and skips silently when
the session or options are not ready. The Activity tab reports progress on three bars: the
**overall** pipeline (scan/download overlap with a growing count, then final queue
percentage and estimated time to finish), the per-run file count, and the current
file with transfer speed.

**Pause/Resume** and **Stop** are cooperative; an in-flight request may finish or time out
first. Use **Save preferences** to retain output folder, creators, filters, layout options,
browser choice, rules source, creator/date modes, the theme, the session-autoload choice,
and the automatic-check interval. Session values are never saved, and settings are not
saved automatically.

### Existing files and compatibility

The original script stored files under a creator folder using `photos/`, `videos/`,
`audios/`, and category prefixes such as `messages/` or `purchased/`, with multi-photo
posts in a `<date>_<post-id>/` album folder. 2.x uses the same shape for normal
photo/video/audio, so exact matches are cheap. The original used `gifs/` where 2.x groups
GIFs under `photos/`; 2.0.3 recognizes both.

On an exact miss, a lazy metadata-only index scans that creator tree once for
`<something>_<media-id>.<extension>` files (ignoring zero-byte files, `.part` files,
symlinks, and files outside recognized media folders). No full-file hashing is done — the
payload is not read just to build the index. Adopted files are recorded in
`.ofdl.sqlite3` and counted as Existing, so later runs locate them directly.

### Dates and previews

- **All dates** (default, safest for repeat runs): existing files are skipped while newly
  added media on older posts is still discovered. Days/Date inputs are disabled and
  ignored.
- **Last N days** / **Since date** use UTC with local filtering across messages,
  purchases, posts, and stories. Days must be 1–36500 when active; Date must be valid
  `YYYY-MM-DD` when active. Undated media is excluded under a date filter.
- **Allow previews** is off by default. Permitted previews get distinct `_preview`
  filenames and manifest identities, so they do not masquerade as full files.
- Normal Downloads skip inaccessible media, DRM-marked media, DASH/HLS manifests,
  and unsupported external hosts. The dedicated Live recording tab supports the
  clear HLS subset described below.

### Live recording

#### Automatic recording (recommended for WebRTC livestreams)

1. Load your account in **Session**. Select Chromium, Chrome or Edge there.
2. Open **Live recording → Automatic live recording**. If needed, click
   **Set up browser** once; this installs the optional Playwright dependency and
   Chromium when selected, then checks that the selected browser can launch.
3. Choose the output folder, then enable **Automatically record live streams**.
   Leave creator filters blank to watch all current subscriptions. No playlist
   URL or Agora credentials are required.

Monitoring runs only while the app is open, with a usable session. The opt-in,
folder, filters, interval and concurrency are remembered. With the existing
**Load saved session automatically** option and an explicitly saved OS-keychain
session, monitoring can resume after reopening. No OS background service is created.

Default checks are every **60 seconds after a scan** (configurable 30–3600), with
**two** parallel recordings (configurable 1–4). Detection is polling, not an instant
stream-start notification: paging, browser startup, verification and waiting for a
recording slot add latency, so the beginning can be missed. Already-live streams
are also recorded when monitoring is enabled. Explicit creator filters still apply
only to active subscriptions; skip entries take precedence.

The monitor reads subscription `hasStream` flags and active-stream metadata, then
opens `/{creator}/live` in an isolated temporary browser. The account session cookies
are scoped to the website; no blanket signing headers are added to browser requests.
Browser login or verification may need interaction in the visible recording window.
No persistent browser profile is saved by this application. Browser recording uses
the Session proxy; with a proxy, direct non-proxied WebRTC UDP is disabled, which can
prevent playback if the service has no compatible relay. A Chromium-compatible
Session user agent is reused; other browser user agents are not imposed on Chromium.

Recordings are written incrementally under `<output>/<creator>/` as `.webm.part`.
Stop, duration limit and a normal player-track end flush pending chunks and rename
to `.webm`. Failures retain the partial file. A silent source is clearly marked
**VIDEO ONLY**. Two consecutive successful scans with a missing stream stop its
recording; 90 seconds without video progress also stops capture. Failures retry
after 60 then 120 seconds, with at most three recording attempts per stream.

**Stop selected stream** saves/stops that stream and suppresses its ID for the
current monitoring session, leaving the poller running. A new stream ID may record
again. **Stop monitoring and save all** disables the opt-in and stops all live work.
Closing the application saves/stops workers but retains the opt-in for next launch.
Changing session/proxy/live settings stops current captures before starting a new
monitor with the new settings. Deduplication is in-memory per monitoring session;
restarting monitoring during the same live event creates a new recording part/file.

Account HTTP writes are blocked except the exact stream's empty `/look` viewing
heartbeat. Purchases, subscriptions, tips and `/join` POSTs are not enabled. If the
player requires another account action, recording fails visibly rather than enabling
it. Automatic capture currently requires an accessible WebRTC video track; arbitrary
players, paid-entry flows and encrypted media are not supported. Platform endpoint
and player behavior are based on third-party source observations, not an official
contract. **No real-account livestream has been tested in this application.**

#### Manual HLS recording

1. Open a live stream you can already watch on the website. In the browser developer
   tools, open **Network**, filter for `m3u8`, and copy the request URL. Use a muxed
   playlist (audio and video together). This advanced mode is separate from
   automatic WebRTC browser recording.
2. Open **Live recording → Manual HLS URL**, paste the URL, choose an output folder and label, and
   optionally set **Maximum minutes** (`0` records until Stop or stream end).
3. Click **Start recording**. The tab shows complete segments, recorded media time,
   bytes, elapsed time and the local file path. Use **Stop and save** in this tab
   to finish. The Downloads footer's Stop/Pause controls only the download pipeline.

Manual HLS recording uses the explicit Session proxy and user agent captured at Start, but
never forwards account cookies or signing headers to the media server. Signed URLs
stay in memory and are not saved in preferences/logs. Recording can run alongside
normal downloads; closing the app stops both workers and waits for cleanup.

Supported: HTTPS OnlyFans/CDN hosts, clear HLS master/media playlists, muxed MPEG-TS
(`.ts`) and fragmented MP4 (`.mp4`) with one initialization section. A master selects
the highest-bandwidth compatible rendition. Live recording starts near the live
edge (up to three already advertised segments), then follows new sequence numbers.
Finished playlists are read from the beginning.

Unsupported: encrypted/DRM playlists, separate audio renditions, byte-range segments,
DASH, and non-OnlyFans hosts. Sequence gaps, restarts, initialization changes and
discontinuities stop recording explicitly; two minutes without new segments also
stop it. Refresh an expired URL manually and start a new recording. These conditions
leave `.part` files containing the segments already received. Stop, normal stream end
and duration limits rename completed recordings to their native `.ts`/`.mp4` suffix.
Live files do not use the ordinary download-resume manifest. No purchase or
subscription is initiated by the recorder.

## File handling

By default output goes under `~/Downloads/OnlyFans`, with per-creator media folders;
photo albums and category subfolders are configurable. Files stream to `.part` and are
atomically renamed only after successful completion. Resume needs a usable
ETag/Last-Modified validator, a matching byte-range response, and consistent length
info; otherwise the file restarts safely. Length checks are not full decode or
cryptographic verification.

A local `.ofdl.sqlite3` manifest records media IDs, relative paths, sizes, and completion
times — never session cookies or signed media URLs. It deduplicates across categories and
notices size changes. A process lock prevents two instances from writing the same output
folder at once.

## Session storage and privacy

By default credentials stay in the running app's memory and are not written to
preferences, the rules cache, the manifest, logs, or any plaintext session file.
**Save session securely** explicitly stores to a supported OS credential store via
`keyring` (Windows, macOS, Secret Service, KWallet); unsupported/plaintext backends are
refused. **Load saved** loads on demand; **Forget saved** deletes the entry; **Clear
fields** clears the form but not the saved entry. **Load the saved session automatically
when the app starts** performs that load at launch; it stays quiet if nothing is saved.
None of these revoke the server session — use the website's account/security controls for
that.

Nonsecret settings and the rules cache live under:

- Windows: `%LOCALAPPDATA%\OnlyFansDL`
- macOS: `~/Library/Application Support/OnlyFansDL`
- Linux: `$XDG_CONFIG_HOME/onlyfans-dl` or `~/.config/onlyfans-dl`

There is no app telemetry or hosted login service. Account API requests go to the
platform; media requests go only to validated HTTPS platform hosts; the public rules
request uses a separate session and carries no account credentials.

## Signing rules and troubleshooting

Signing rules are loaded as schema-validated JSON from a configurable HTTPS source or
local file (not executed code). The default is the community-maintained DATAHOARDERS
file:

```
https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json
```

It is fetched on demand, cached for 15 minutes, and refreshed with **Refresh rules**. No
old rules are silently bundled as a fallback, and a schema-valid file is **not** proof the
platform currently accepts it.

401 usually means a fresh session. For 403, verify access, current session/rules, and
system clock; a `403` that returns HTML is a Cloudflare protection page rather than an
API answer, so also use the same network the session was captured on and let the
browser-like transport run. 429 and transient errors retry with backoff and respect
reasonable Retry-After values. A scan that failed for some categories is reported as **finished with
issues**, not a complete backup. TLS verification stays enabled; API/rules redirects are
refused; media redirects are revalidated.

## Command line

The CLI uses the same engine. No arguments opens the UI.

```bash
python onlyfans-dl.py --help
python onlyfans-dl.py --cli creator_name --use-keyring --scan-only
python onlyfans-dl.py --cli creator_name --use-keyring --days 7 --output ./downloads
python onlyfans-dl.py --cli --all-subscriptions --skip creator_to_skip --use-keyring
python onlyfans-dl.py --cli --test-session --use-keyring
python onlyfans-dl.py --cli --refresh-rules
```

Run these with your virtual environment's Python. Credentials saved by the GUI can be
reused with `--use-keyring`. `--session-stdin` accepts a copied request, headers, or
session JSON from stdin (never executed). `--days 0` means all dates (the old numeric
"latest filename" shortcut is gone). Environment variables `OFDL_USER_ID`,
`OFDL_USER_AGENT`, `OFDL_X_BC`, and `OFDL_SESS_COOKIE` are a fallback for controlled
environments; there are intentionally no cookie/token command-line arguments.

Exit codes: `0` success, `1` error, `2` completed with reported issues, `130`
interruption.

## Package layout and tests

| Path | Role |
| --- | --- |
| `onlyfans-dl.py` | Thin launcher (UI or `--cli`) |
| `ofdl/auth.py` | Session import and OS credential-store storage |
| `ofdl/api.py` | Rules, signing, pagination, bounded retries, diagnostics |
| `ofdl/downloads.py` | Discovery, queueing, transfers, manifest, legacy adoption |
| `ofdl/pipeline.py` | Bounded cancellable discovery-to-download worker |
| `ofdl/live.py`, `ofdl/ui_live.py` | Clear HLS recording and independent recorder tab |
| `ofdl/live_watch.py` | Subscription live discovery and bounded recording supervisor |
| `ofdl/live_browser.py`, `ofdl/live_capture.js` | Temporary browser WebRTC audio/video capture |
| `ofdl/browser.py` | Optional Playwright login helper |
| `ofdl/preview.py` | Off-thread image/video preview generation |
| `ofdl/ui.py` | Tk/ttk desktop interface |
| `ofdl/cli.py` | Command-line interface |
| `ofdl/settings.py`, `ofdl/common.py`, `ofdl/diagnostics.py` | Shared support code |

Run the local tests from the package folder:

```bash
python -m unittest discover -s tests -v
```

GUI tests need a display; without one they are skipped. On headless Linux with Xvfb:

```bash
xvfb-run -a python -m unittest discover -s tests -v
```

Tests use synthetic account data and simulated HTTP responses — no real credentials.
When FFmpeg/ffprobe are available, a local integration test generates synthetic HLS,
records both supported containers and verifies that their audio/video can be decoded. See
[CHANGELOG.md](CHANGELOG.md) for implementation details, [TEST_REPORT.md](TEST_REPORT.md)
for limitations, and `docs/ui-*.png` for rendered UI screenshots (retained from 2.0.0, so
they show layout rather than the latest version label).

### Technical references

- Playwright request headers: https://playwright.dev/python/docs/api/class-request
- Browser context isolation: https://playwright.dev/python/docs/browser-contexts
- Chrome Network panel: https://developer.chrome.com/docs/devtools/network/reference/
- Keyring backends: https://keyring.readthedocs.io/en/latest/
- Requests sessions/TLS/timeouts: https://requests.readthedocs.io/en/latest/user/advanced/
- Tkinter threading: https://docs.python.org/3/library/tkinter.html

These explain the tooling; they are not an endorsement of this downloader or proof of
live platform compatibility.
