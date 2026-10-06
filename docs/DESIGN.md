> Historical initial-release document. The 2.0.1 and 2.0.2 changes are
> documented in ../CHANGELOG.md and ../README.md; the current test evidence is
> in ../TEST_REPORT.md. In particular, 2.0.2 removes the subscription picker.

# Desktop downloader design

Historical 2.0.0 design/implementation record. The 2.0.1 corrective changes and
verification are described in HTTP_400_FIX.md and ../TEST_REPORT.md.

The uploaded script is the basis: keep its profile/content categories and file layout,
but remove editable session constants and unsafe global mutable state.

Assumptions: a local desktop application, Python 3.10+, single-account session, and
only media the account is authorized to access. No hosted service, password storage,
DRM decryption, purchase automation, CAPTCHA workarounds, or browser-profile scraping.

Use a Tk/ttk UI with Session, Downloads, Activity and Help tabs. A background worker
communicates with the UI through a queue; Tk variables are read only on the UI thread.
Support login in a new temporary visible browser, explicit cURL/raw-header/JSON/HAR
import, manual fields, optional OS credential storage, and a session test. Never execute
imported commands. Persist only nonsecret preferences by default.

Separate auth parsing/storage, browser capture, signing/API, downloads, and UI.
Load public signing rules as validated JSON from an editable source with an explicit
refresh action and a short-lived cache. The source uses a separate unauthenticated
HTTP session. API requests are signed from their prepared, URL-encoded path.

Use sequential streaming downloads, a conservative request interval, bounded retries,
finite read/connect timeouts, pause/stop, validated resumable partial files and atomic
completion. Restrict media to HTTPS OnlyFans hosts and validate every redirect. Do not
forward API credentials to the CDN or rules provider. Deduplicate with a local SQLite
manifest; filter dates in UTC for every content category. Fetch purchases once per run.

Provide a command-line mode, launch scripts, dependency files, tests and a changelog.
Do not claim live platform compatibility without an authenticated integration test.
