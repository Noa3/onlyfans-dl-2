# Changes from the supplied onlyfans-dl.py

## Desktop 2.8.1 — 2026-11-25 — Firefox session import and network output folders

- Add `import_firefox_session.py`: reads the already logged-in `onlyfans.com`
  session from a local Firefox profile (`sess`/`auth_id` cookies and the
  `bcTokenSha` localStorage value used for `x-bc`), derives the user agent from
  that profile's Firefox version, and stores the result in the OS credential
  store. Secret values are never printed or written to disk; `--dry-run` only
  checks that a session can be read. Useful when the fresh-browser helper is
  inconvenient or asks for another login.
- Accept browser-style network folders such as `smb://host/share` as the output
  folder. On KDE they are resolved through KIO-FUSE with the credentials already
  stored by Dolphin/KWallet, so downloads and live recordings can write directly
  to an SMB share. Web and unknown URL schemes are rejected with an actionable
  message instead of a generic `OSError`; plain local paths behave as before.
- Apply the same output-folder resolution to Downloads, manual live recording and
  automatic live monitoring. Add path-resolution tests, including the
  `pathlib`-mangled `smb:/...` form produced by the GUI.
- **Save session securely** now also persists the output folder with the other
  nonsecret preferences, so a network folder does not have to be re-entered after
  a restart. Unrelated saved preferences are preserved and credentials are still
  never written to `settings.json`.
- Document the KIO-FUSE behavior and the Arch/CachyOS `tk` package in the README.

## Desktop 2.8.0 — 2026-10-22 — free/trial claims and deeper discovery

- Re-enable **Subscribe / claim selected** for zero-price offers after explicit
  confirmation. Free pages are re-read immediately before the POST and refused when
  no longer free or already subscribed; shared trial codes are sent to the trial
  claim endpoint. Code-less trials, paid pages and subscribed rows stay read-only.
- Never retry POSTs. Record successful claims in `free_trials.json` (trial codes as
  hashes) and update row status. Warn that trials auto-renew at the regular price.
- Search expired creators' newest chat messages for fresh trial links. Add a Deep
  scan option for more chat pages/partners, lists and list users, plus a table search
  box. Persist the search text and deep-scan preference.
- Cover zero-price gating, price-change refusal, duplicate-code refusal, server-side
  subscription detection, expired-chat discovery, deep limits, UI confirmation and
  non-actionable row skipping with tests.

---

## Desktop 2.7.0 — 2026-10-22 — opt-in automatic livestream recording

- Add persisted automatic monitoring of active subscriptions, with optional creator
  include/skip filters, 30–3600-second polling and 1–4 simultaneous recordings.
- Discover live flags and stream IDs through signed GET requests; adapt the browser
  MediaRecorder/audio-tap approach from OF-Scraper-GUI Live Stream Monitor (MIT notice
  included). Native Agora SDK capture is not used.
- Record WebRTC video plus direct, detached-element or Web Audio audio in temporary
  browser contexts. Write ordered, bounded chunks to WebM partial files, flush on
  stop/end/duration limit, retain partial recordings on errors, and mark video-only
  recordings explicitly. Stop captures after two successful offline scans or a stall.
- Add one-click browser setup, per-stream status/file table, stop-selected, stop-all,
  per-session ID deduplication, three-attempt cooldowns and API error backoff. Settings
  changes stop and restart monitoring; closing waits for cleanup and retains opt-in.
- Restrict browser account writes to the exact empty viewing heartbeat; block
  purchases, subscriptions, tips and join POSTs. No persistent browser profiles,
  token dumps or plaintext credential preferences are introduced.
- Verify discovery, scheduler cancellation/dedup/retry behavior, UI opt-in/shutdown,
  and locally generated browser audio/video capture with FFmpeg decoding. Platform
  API/player compatibility has not been tested against a real account livestream.

---

## Desktop 2.6.0 — 2026-10-22 — overlapping downloads and live recording

- Download discovered media while API paging continues, through a bounded 64-item
  queue. The media thread owns its HTTP session, SQLite manifest and folder lock;
  summary snapshots are serialized. Failure/cancellation joins the worker and keeps
  completed files. Scan-only and empty scans create no output files.
- Show growing totals during discovery and final progress/ETA only after scanning.
- Add an independent Live recording tab accepting direct accessible HTTPS OnlyFans
  HLS URLs, with Start, Stop-and-save, output directory, label and duration limit.
  Capture clear muxed MPEG-TS or fragmented MP4, follow sequence numbers, preserve
  partial recordings on errors and save completed segments on stop.
- Reuse the explicit proxy without forwarding account credentials. Validate playlist,
  rendition, initialization, segment and redirect URLs. Reject encrypted playlists,
  separate audio tracks, byte ranges, sequence gaps and unsupported discontinuities.
- Add `m3u8` for playlist parsing. Recording itself does not require FFmpeg.
- Add deterministic concurrency/cancellation tests, simulated HLS progression and
  error tests, independent recorder UI lifecycle tests, and optional local FFmpeg
  encode/record/decode integration coverage. No live-account endpoint validation is
  implied; automatic stream URL discovery is not implemented.

---

## Desktop 2.5.1 — 2026-10-22 — review fixes

- Disable automatic free subscriptions and trial claims until a server-enforced
  zero-price contract is verified. Scanned prices do not establish that guarantee;
  a fresh lookup alone cannot prevent a price-change race. Links open for manual
  website review. Existing paid subscriptions and purchases remain downloadable.
- Stop duplicate deletion when the keeper is missing, changed, outside the library,
  or uses links/junctions. Re-hash the keeper before each deletion, detect file
  changes during verification, and propagate cancellation rather than swallowing it.
- Restrict automatic API retries to GET; never retry a POST after a timeout/5xx/429.
- Stream snapshot rows to the UI, retain partial results after cancellation/errors,
  and mark scans incomplete when an optional source fails. Preserve prior list-page
  results and continue other lists. Authentication failures and rate limits stop scans.
- Exclude password-only proxy URLs from plaintext preferences; snapshot proxy values
  before worker launch and include proxy changes in in-flight session validation.
- Count unsupported streaming manifests separately from explicit DRM metadata.
- Extract tab layouts into `ui_offers.py` and `ui_duplicates.py`.
- Add regression coverage for keeper changes, account-action blocking, POST retries,
  source failures, partial results, proxy threading/privacy, and streaming counters.

---

## Desktop 2.5.0 — 2026-10-22 — broader free/trial scan and visible progress

### Scan sources

- Add **inbox chat partners**: recent chats from `/chats` are read, so creators who
  wrote to you are covered even when you never subscribed. Up to 80 unknown partners
  get a profile check; their newest messages are included in the trial-link scan.
- Add **custom lists**: `/lists` and `/lists/{id}/users` are read, covering favorites
  and other lists you maintain. Bounded to 20 lists / 400 users per scan.
- Add **website promo banners**: `/users/promotions` banners are followed. A banner
  that contains a trial link becomes a claimable trial row; a banner that points to a
  profile is classified like any other profile. Banner images are never downloaded.
  Banners that are paid, unreadable or not zero-price are counted and skipped.
- Still no platform-wide scan: no endpoint enumerates all creators. Inbox, lists and
  banners only cover creators who contacted you, are in your lists, or are advertised
  to your account.
- One row per creator; an actionable shared trial code replaces a code-less offer.

### Feedback

- The tab now has an inline indeterminate progress bar and a status line that names the
  current phase (`reading subscriptions`, `inbox`, `custom lists`, `promotions`,
  `chat messages`, `entered names`) with done/total counters.
- Start, stop, failure and finish each update that line, so a scan never looks idle.

### Paid actions

- Explicitly no new paid subscription and no paid content purchase. Only zero-price
  free pages and shared free-trial codes can be acted on, after confirmation.
- Existing paid subscriptions and already purchased content stay downloadable through
  the normal Downloads tab, including the Purchased category. Download behaviour is
  unchanged.

### Tests

- 220 local tests executed: 219 passed, 1 skipped (including inbox, custom-list,
  promo-banner, paid-banner rejection and progress-event coverage).

---

## Desktop 2.4.0 — 2026-10-22 — explicit proxy/VPN support

### Network

- Add a proxy field on the Session tab and `--proxy` in the CLI. Accepted schemes:
  `http`, `https`, `socks5`, `socks5h` (remote DNS). Validation rejects paths, query
  strings, fragments, control characters and out-of-range ports.
- The same proxy is applied to the account API, public signing-rules download, media
  downloads and the Playwright browser helper. Media must use the same exit IP as the
  API because OnlyFans CDN URLs are signed for the requesting IP.
- Ambient `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` and `.netrc` remain ignored; only the
  explicit value is used.
- A proxy URL containing credentials stays in memory and is not persisted to
  `settings.json`; changing it invalidates the session-test status.
- SOCKS needs PySocks, added to `requirements.txt`. A missing PySocks now produces a
  clear message instead of a generic connection failure.
- Help text documents VPN pitfalls: Cloudflare HTML 403 on VPN ranges, IP-bound signed
  media URLs, rotating exits, per-IP 429 limits and split tunnels.
- `Transport` forwards method and JSON body with the proxy on both backends.

### DRM reporting

- `media_task` returns the dedicated reason `drm` for DRM-marked media and `.mpd`/
  `.m3u8` sources. `RunSummary.drm` counts them; Activity and the CLI summary show
  `DRM-protected (skipped)` separately from other unavailable media.
- DRM decryption, key extraction and paid claims remain deliberately unsupported.

### Tests

- 214 local tests pass (208 retained plus proxy validation, proxy propagation to API /
  rules / media, credential-safe preference persistence, DRM counting).

---

## Desktop 2.3.0 — 2026-10-22 — duplicate library checker

### Interface

- Add a **Duplicates** tab (4) with a library folder field, scan, keeper selection,
  delete and open-folder actions. Activity moved to tab 5.
- The tree groups identical files: each group shows the copy size, number of copies and
  reclaimable bytes; file rows show a relative path, size and Keep/Duplicate state.
  Group rows and files can be selected; selecting any file row allows making it the
  keeper.
- The folder and all scan results are local; the chosen folder is saved in preferences.

### Detection and deletion

- Grouping is content based (SHA-256), not name or media-ID based, so re-downloads and
  old folder layouts are found. Only files with an identical size are hashed.
- Hash results are cached in `file_hashes.sqlite3` (config folder) by path, size and
  `mtime_ns`; unchanged files are not re-read on the next scan.
- Symbolic links are never followed; hidden files, `.part` files and non-media
  extensions are skipped; a symlinked root is refused.
- The keeper is the copy recorded in `.ofdl.sqlite3` when possible, otherwise the
  shallowest/shortest path with deterministic tie-breaks.
- Delete other copies requires confirmation, stays inside the chosen folder, skips
  non-regular files, verifies size, and re-hashes every extra copy immediately before
  removal. Changed files are kept and reported. Deletion bypasses the recycle bin.
- New `ofdl/duplicates.py`. No API calls and no uploads are involved.
- 208 local tests pass (197 retained plus duplicate-detection, cache, keeper and
  deletion-safety coverage).

---

## Desktop 2.2.0 — 2026-10-22 — free & trial subscriptions tab

### Interface

- Add a **Free & trials** tab (3) before Activity, with source toggles for active
  subscriptions, expired subscriptions, newest chat messages, and an optional creator
  list, plus a scan summary.
- Results are a sortable table: creator, type (Free page / Free trial), price, source,
  status and details. Filters: All / Free only / Trials only and All statuses /
  Available only / Already subscribed. Clicking a heading toggles ascending/descending.
- Rows for pages you already subscribe to are marked and cannot be acted on, so a
  second subscription is impossible from this tab.
- **Subscribe selected** only sends after an explicit confirmation dialog listing every
  row. **Open profile / link** opens the creator page or the shared trial link.
- Source choices, extra creator names and filters are remembered in preferences.
  Help text gained a FREE & TRIALS section.

### Detection and actions

- Free page: regular price (`subscribePrice` / `subscribedByData.regularPrice`) is 0.
- Free trial: current price 0 or a claimable 0 promotion while the regular price is
  paid. These rules mirror the community OF-Scraper free-trial filter. Paid offers are
  never returned, so nothing above 0 can be purchased from this tab.
- Trial links are found in the newest messages (`…/trial/<code>`), deduplicated, and
  claimed with `POST /api2/v2/trials/claim`. Free pages use
  `POST /api2/v2/users/{id}/subscribe`.
- Trial offers with no code remain informational; they are never guessed at.
- `free_trials.json` remembers free subscriptions and claimed trial codes. Codes are
  stored as a SHA-256 prefix only, and a matching row is marked "Claimed earlier" on
  the next scan.
- New `ofdl/offers.py` holds detection, scanning, claiming and the history store.
  `ApiClient` gained a signed JSON `post()` sharing the GET path/query signature
  contract, empty-body tolerance, and `paginate(..., subscription_type=...)` for
  expired subscriptions.
- 197 local tests pass (195 retained plus offers, POST and transport coverage).

---

## Desktop 2.1.1 — 2026-10-12 — Cloudflare-compatible API transport

### Reported issue

A user reported `HTTP 403 ... An HTML page was returned instead of API JSON`. That page
is Cloudflare refusing the request before the API is reached, not a wrong session. The
current client was verified live against a real account and returned HTTP 200 JSON on an
unchallenged network, so the failure is network/session specific rather than a broken sign.

### Changes

- Send API requests with the same optional headers a real same-origin XHR sends
  (`Accept-Language`, `sec-fetch-dest/mode/site`, `priority`) alongside the existing
  authentication and signing headers. Rules may still remove individual optional headers.
- When the optional `curl_cffi` package is installed, API calls use a Chrome TLS
  impersonation profile so the handshake looks like a browser's instead of OpenSSL's.
  This addresses the passive Cloudflare fingerprint that produces the 403 HTML page.
  The client falls back to plain `requests` automatically when `curl_cffi` is absent or
  when a caller injects its own session.
- The public signing-rules download deliberately keeps using plain `requests`.
- A genuine JavaScript/managed Cloudflare challenge is still never bypassed; the new
  transport only makes the request look like a normal browser request.
- Clearer 403-HTML advice: the session is often tied to the network it was created on.
- `curl_cffi` added to `requirements.txt`; `ofdl/http.py` holds the transport.
- 172 local tests pass (168 retained plus browser-header and transport-selection tests).

---

## Desktop 2.1.0 — 2026-10-07 — dark mode, session autoload, scheduled checks, overall ETA

### Interface

- Add a header **Dark mode / Light mode** toggle that recolors the whole app immediately
  (ttk theme, window, tab, entry, list, scrollbar and progress colors) and is remembered
  in preferences.
- Remove the old "A local interface for your own account…" header subtitle.
- Add a **Load the saved session automatically when the app starts** checkbox on the
  Session tab. It reads the OS credential store on launch, quietly logs instead of
  prompting when nothing is saved, and never writes a plaintext session.
- Add **Check for new content automatically** on the Downloads tab, every 1–10080 minutes.
  The timer runs only while the window stays open (never as an OS autostart) and skips
  silently, without a dialog, when the session or options are not ready.
- Activity: the log is compacted (smaller fixed font, narrower column) so the **Latest
  media** preview area expands. The preview frame is now elastic and the **Open file**
  button is pinned to the bottom, so it stays visible at any window height.
- Add a third, **overall** progress bar with an estimated time to finish. It animates
  during creator discovery/paging, then shows queue percentage and ETA during transfer,
  alongside the existing per-run and per-file bars.

### Notes

- Theme, autoload and automatic-check values are nonsecret preferences; session values
  remain excluded from preferences exactly as before.
- 168 local tests pass (163 retained plus new theme, autoload, auto-check, pipeline/ETA
  and header-subtitle checks). See TEST_REPORT.md.

---

## Desktop 2.0.3 — 2026-10-06 — Activity preview and large-library legacy adoption

### Activity preview

- Split Activity into the existing log/results area and a right-side **Latest media**
  panel with creator/type, relative filename, size, preview status and **Open file**.
- Newly completed images/GIFs are reduced to a maximum 320×240 PNG thumbnail on a
  dedicated background worker. Tk image creation remains on the main UI thread.
- Newly completed videos optionally get one small poster frame when `ffmpeg` is already
  available in PATH. No video playback loop or bundled ffmpeg dependency was added.
  Audio and unavailable video previews use lightweight placeholders.
- The preview queue retains only the newest pending completion. Existing/legacy files
  do not emit preview work, preventing an 80k-file migration from becoming an 80k
  thumbnail job.
- Add Pillow as the only new normal dependency.

### Existing files from the supplied original script

- Confirmed that the original default layout already matches the 2.x layout for normal
  photos, videos and audio: creator/category/media-folder/date_media-id. Multi-photo
  album folders also use the same date/post-id shape.
- Confirmed one concrete layout mismatch: the original script used `media["type"] +
  "s"`, therefore GIFs were stored under `gifs/`; 2.x groups GIFs under `photos/`.
- Keep the cheap exact-path check first. Only on an exact miss, lazily index that
  creator's tree once per run by media ID and compatible file type. This finds old
  category-subfolder, flat-folder, album and GIF layouts without reading file contents.
- Ignore zero-byte files, `.part` files, symlinks and files outside recognized media
  folders during legacy matching. Found legacy files are adopted into `.ofdl.sqlite3` and counted as
  Existing instead of downloaded again.
- Batch non-destructive legacy-adoption manifest commits (flush every 500 records and
  on close). Freshly downloaded media still receives an immediate durable manifest
  commit. A crash before a legacy batch flush is harmless: the file remains on disk
  and is rediscovered on the next run.

### Verification

163 local tests pass under Xvfb, including the previous 149-test suite plus new legacy
layout, manifest-batching, preview helper, completion-event and actual Tk preview-panel
regressions. See TEST_REPORT.md for exact commands and limitations.

---

## Desktop 2.0.2 — 2026-10-06 — simpler creator selection and date fields

### Creator selection

- Remove the separate subscription picker, its Ctrl/Shift highlighting, and the
  Load subscriptions button. The Downloads tab is now the sole selection source.
- Add an explicit **All active subscriptions (automatic)** mode, selected by default
  with new/empty preferences. Each Start download or Scan only fetches a fresh active
  subscription list without any prior manual selection step.
- **Skip creators** always wins: blank means no exclusions; otherwise only matching
  names are excluded. Matching uses the same case-insensitive usernames, @names and
  profile-URL parsing as the rest of the app.
- Keep **Only these creators (optional)** for explicit subsets and accessible purchases
  from creators no longer in the active subscription list. Its text box is disabled
  and ignored in automatic mode, while retaining the user's text for later use.
- Add a live scope summary, including effective included/skipped counts and a name
  preview for explicit lists. The Activity log reports actual counts after discovery.
- Empty explicit lists, no active subscriptions and all-excluded scopes produce clear
  messages, never a silent fallback to downloading everyone. Subscription pagination
  must finish successfully before any creator content scan begins.
- Changing options, opening the app and saving preferences do not start downloads.
  The run uses a snapshot of the selected options taken when the button is clicked.

### Dates and saved settings

- All dates disables and ignores both Days and Date. Last N days enables only Days;
  Since date enables only Date. Disabled fields are visibly greyed out and cannot be
  edited. Only the active date filter is validated/applied.
- Worker completion restores the correct conditional states instead of indiscriminately
  enabling inactive inputs. Creator/date controls are locked during background work.
- Save the explicit creator/date modes plus dormant field values. Retained values do
  not accidentally activate another date filter on restart.
- Migrate old days=0 (All dates) saves to a usable dormant default of 7. A literal zero
  in new-format settings is preserved and rejected only if Last N days is activated.
- Preserve old nonempty creator lists in the visible Only these creators mode instead
  of silently broadening their scope. Choose All active subscriptions and Save
  preferences once to make automatic scope the saved choice.

### Verification and scope

149 local tests pass (110 retained plus 39 additional scope/UI/settings tests), including
actual Tk widgets under Xvfb and a complete automatic-mode download through the real
engine into a temporary directory with simulated HTTP media responses. Screenshots of
the updated Downloads tab are actual Linux UI renders with synthetic names.

The authentication, signing, browser-capture, safe-diagnostics and CLI source files are
byte-for-byte unchanged from 2.0.1. Installers/dependencies and media transfer code are
also unchanged. No live service request, account login, real media download, Windows
launcher execution or OS-keychain integration was tested. See TEST_REPORT.md.

---

## Desktop 2.0.1 — 2026-10-06 — session / HTTP 400 follow-up

### Confirmed defect in the previous package

The authenticated signer included the imported account ID in the signature, then
processed provider `remove_headers` metadata and could delete the outgoing `user-id`
header. The user's selected public rules source included `"remove_headers": ["user-id"]`
when inspected. The old unit test **expected that incorrect deletion**, so the original
80 passing tests did not catch it. A replacement test fails on 2.0.0 and passes with
this fix. A separate simulated endpoint recomputes a signature from the emitted request.

This confirms the client inconsistency, **not** the exact cause of the user's live
HTTP 400. The old log omitted response details and no real account session was used
for debugging. Public rule acceptance and browser/session reuse remain unverified.

### Fixes and focused improvements

- Preserve the authenticated `user-id` header, apply optional header removals first,
  and sign using the actual outgoing identity header. Literal header names containing
  underscores are no longer rewritten into different hyphenated names.
- Add bounded, allowlisted API error diagnostics: HTTP status, response type, recognized
  message category, a small numeric error code, public rule-content fingerprint, identity
  header presence, and a coarse server-Date/clock comparison. Arbitrary response text,
  account fields, request URLs, cookies and raw browser exceptions are not logged.
- Do not equate `Please refresh the page` with proof of an invalid signature. Unknown
  errors stay unknown. HTTP 400 is not blindly retried; HTTP 200 error objects are
  still failures. Malformed/oversized error bodies do not erase the original HTTP status.
- Verify that `/users/me` identifies the imported account before reporting a successful
  session test. Other creator profile lookups remain independent of that check.
- Browser capture now listens for a **completed** `/users/me` GET with JSON identifying
  the same account. A generic/public HTTP 200, HTML page or another account's profile
  cannot count as successful capture. Login password/2FA POST bodies are never read.
- Separate browser-launch, missing-browser, timeout and browser-disconnection messages.
  Cleanup errors no longer discard a successfully captured session.
- Reset private-field masking on import/load, Clear fields, and Test session. A failed
  or cancelled session test is not marked ready. Edits invalidate the tested status;
  an in-flight test cannot certify different inputs entered while it was running.
- Bump the visible application/CLI version to 2.0.1 and add `docs/HTTP_400_FIX.md`.

No new dependency, automatic provider switching, stored browser profile, stealth mode,
DRM handling or access-control bypass was added. The download engine and launchers
are unchanged. The full ZIP is required; the entry-point script alone is not the fix.

### Local verification

110 tests pass, including actual Tk tests under Xvfb and synthetic HTTP/browser
responses. The distribution is also tested after fresh extraction. See TEST_REPORT.md
for exact commands, environment and limits. Live login/media downloads and Windows
batch execution are not verified.

---

## Desktop 2.0.0 — 2026-10-06 (original release)

This is a modular replacement package, not a patch that must be pasted into the
original file. The uploaded original was left unchanged.

### User interface

Added a Tk/ttk desktop application with Session, Downloads, Activity, and Help/privacy
tabs. Configuration no longer requires editing Python constants. The Downloads tab
includes creator entry, a searchable active-subscription selector, creator exclusions,
folder browsing, media/category choices, photo albums, category subfolders, UTC date
filters, and nonsecret preference saving.

Added scan-only mode, per-file and overall progress, transfer speed, outcome counts,
pause/resume, stop, and output-folder opening. Network work runs on a worker thread;
only the main thread updates Tk. Exceptions visible to users omit raw secret-bearing
request details. Logs can be saved explicitly and are bounded in the UI.

Fixed two issues discovered while testing the new interface: success callbacks could
leave a stale “working” status, and notebook geometry could hide bottom controls in a
short window. The footer now retains space; setup/download tabs scroll. Both behaviors
have regression tests. Screenshots are actual renders, not visual mockups.

### Easier USER_ID / USER_AGENT / X_BC / SESS_COOKIE setup

Added an opt-in visible browser helper using Playwright. It opens an isolated context,
lets the user log in normally, observes successful API GET headers, and fills all four
values together. It does not read login POST bodies or saved normal-browser passwords,
write a persistent browser profile, or attempt challenge/automation-detection bypass.
Chromium and installed Chrome/Edge channels are selectable.

Added local copied-request import. It accepts cURL, raw colon-separated or alternating
DevTools request headers, session JSON, and compatible HAR files. cURL is tokenized,
never executed. It understands cookie and user-agent options, common line continuations,
and imports additional request cookies. Numeric USER_ID can come from `auth_id` when
`user-id` is absent. Missing fields, conflicts and sanitized HAR files produce clear
errors without echoing secrets. Imports are limited to 10 MB; API URL-bearing formats
are restricted to the expected HTTPS platform API.

Manual fields remain available and private fields are masked by default. Added Test,
Load, Save, Forget, Clear, explicit clipboard-paste, and optional current-clipboard-clear
actions. No clipboard polling or ordinary-browser profile scanning was added.

### Credential and network safety

Removed source-code credential constants. Credentials stay in application memory
unless the user explicitly saves them to an accepted OS credential store via keyring.
Plaintext/unsupported backends are refused rather than silently used. Preferences,
manifest, rules cache, logs, and download-sidecar metadata omit session values.

Corrected the original outgoing `auh_id` typo to `auth_id`. The misspelling is accepted
only as a legacy import fallback. Conflicting `auth_id` and user-id values are rejected.

The original download path used `verify=False` and disabled certificate warnings.
The replacement keeps certificate checks enabled, sets connection and read timeouts,
and closes response/session resources. Signed API requests, unsigned media transfers,
and public rules downloads use separate sessions. API cookies are not forwarded to
media or rules servers. HTTPS URL checks and redirect policies reduce unintended
credential/URL forwarding. Download paths are sanitized and checked to remain inside
the configured root. Windows reserved filenames are handled.

This is not a formal security audit. OS-user processes may still access credentials;
Python memory is not securely wiped; installed dependencies remain a trust boundary.

### Rules, signing, pagination and failures

Removed the fixed signing-rule object embedded in the original main block, whose
revision began `202502031617`. Rules now load from validated local JSON or a configurable
public HTTPS endpoint, with a 15-minute cache and explicit refresh. They are treated as
data, not executed code. Rules requests do not carry account authentication.

Signing now uses the exact prepared path and encoded query, millisecond timestamps,
fresh per-request headers, and the checksum/format from the loaded rules. Rule-specified
header removal is validated and applied. Nothing in this change proves that the live
service accepts a given rule set or session.

Reworked pagination for list and wrapped-list responses. Added distinct cursors for
messages and chronological posts, offsets for other collections, `hasMore` handling,
repeated-page/no-progress guards, deduplication and a finite safety cap. A later failed
request raises a clear error instead of reusing stale data or an undefined page variable.
Malformed successful JSON is not silently interpreted as an empty complete collection.

Added bounded retries for transient network errors, 429 and server errors; Retry-After
is considered. Authentication/access failures are not retried indefinitely. A category
failure is recorded so the UI cannot label an incomplete scan as a complete backup.

### Download correctness and comfort

Downloads are streamed in bounded chunks and written to `.part` before atomic rename.
A file is counted as downloaded only after successful completion, unlike the original
counter increment before its network request.

Added resumable partial downloads when ETag/Last-Modified and Content-Range checks
support a safe continuation. Ignored ranges restart safely; invalid/mismatched ranges,
encoding surprises, inconsistent lengths and non-media HTML/JSON are rejected. Empty
or truncated transfers do not become completed files. A logical truncation error may
require retrying the run; there is no unlimited retry loop.

Added a SQLite manifest for cross-category media deduplication and size checks of
indexed files. Preview and full versions have different identities. A process lock
prevents two instances from writing the same output folder concurrently. Older nonempty
files can be adopted without fetching them again, but their content is initially
trusted by filename and is not automatically repaired or rehashed.

Fixed nullable media-source handling and replaced the inconsistent preview branch.
GIFs now follow the photo option. Previews are opt-in and separately named; locked or
DRM-marked content is not fetched or decrypted. DASH/HLS manifests and nonplatform
media hosts are skipped.

Purchased content is fetched once per run and matched to selected creators by ID or
username instead of repeatedly comparing against the mutable global PROFILE path.
Date filtering now applies locally across content categories, including messages and
purchases, using UTC. Profile parsing and global mutable configuration were replaced
with validated options and per-run state.

### Intentional behavior changes

- The old `latest()` filename heuristic was removed. All dates plus existing-file
  skipping avoids missing media later attached to an old post. Date filters remain
  available but intentionally narrow what is scanned/kept.
- The CLI uses explicit `--days` / `--since` flags instead of a trailing numeric age.
  A positional `all` or `--all-subscriptions` selects active subscriptions.
- Optional previews are off by default; unsupported/locked/DRM media is skipped.
- Credentials are not accepted as command-line token/cookie flags. Use the GUI,
  explicitly saved OS keyring, or controlled stdin/environment handling.
- The default output directory is the user's Downloads/OnlyFans folder, not whatever
  the current working directory happens to be.
- Preferences save only when requested. Session storage has a separate explicit action.

### Distribution and tests

Added Windows setup/start/browser-helper batch files and Linux/macOS shell equivalents.
Dependencies live in a local virtual environment. Python 3.10+ is the source compatibility
target; the actual test runtime is documented in TEST_REPORT.md. No executable or
platform installer was built.

Added 80 local tests across authentication/preferences/credential-store policy, API
signing/pagination, streaming/resume/filesystem behavior, browser response selection,
CLI parsing, and real Tk UI behavior under a virtual display. HTTP and credential-store
services are simulated in these tests. Live site authentication, current server-side
acceptance, native Windows/macOS behavior and actual OS keychains remain unverified.
