# OnlyFans DL — Desktop 2.0.3

A local desktop UI and CLI built on the supplied `onlyfans-dl.py`. The old
edit-the-constants workflow is gone: you set up a session in a form, pick creators
and filters, watch progress, and a separate, testable download engine does the work.

**Use your own account and only content you are entitled and permitted to save.**
This is an unofficial client. It does not bypass access controls, DRM, or security
challenges. No live authenticated OnlyFans login or download was performed when this
package was built — current signing rules and API behavior may require further
adjustment. See [TEST_REPORT.md](TEST_REPORT.md).

**Extract the entire folder.** Keep `onlyfans-dl.py`, the `ofdl/` package, and the
other files together. The launcher is not a standalone replacement file.

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
`python3-venv` and `python3-tk`. Playwright on Linux may also need browser system
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
download** scans, builds a deduplicated queue, then transfers files.

**Pause/Resume** and **Stop** are cooperative; an in-flight request may finish or time out
first. Use **Save preferences** to retain output folder, creators, filters, layout options,
browser choice, rules source, and creator/date modes. Session values are never saved, and
settings are not saved automatically.

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
- Inaccessible media, DRM-marked media, DASH/HLS manifests, and unsupported external
  hosts are skipped rather than bypassed.

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
fields** clears the form but not the saved entry. None of these revoke the server
session — use the website's account/security controls for that.

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
system clock. 429 and transient errors retry with backoff and respect reasonable
Retry-After values. A scan that failed for some categories is reported as **finished with
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

Tests use synthetic account data and simulated HTTP responses — no real credentials. See
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
