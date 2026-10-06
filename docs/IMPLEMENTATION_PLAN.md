> Historical initial-release document. The 2.0.1 and 2.0.2 changes are
> documented in ../CHANGELOG.md and ../README.md; the current test evidence is
> in ../TEST_REPORT.md. In particular, 2.0.2 removes the subscription picker.

# Desktop downloader implementation plan

Historical 2.0.0 design/implementation record. The 2.0.1 corrective changes and
verification are described in HTTP_400_FIX.md and ../TEST_REPORT.md.

**Goal:** Make the uploaded downloader usable without source-code editing, with safer
session handling and observable, reliable downloads.

**Architecture:** Small Python modules; one Tk UI thread and one cancellable worker.
A CLI uses the same API and download engine. No user-account requests during development.

**Tech stack:** Python 3.10+, requests, Tk/ttk, optional keyring and Playwright.
**Spec:** DESIGN.md.

## Global constraints
- Never execute pasted shell commands or persist credentials in preferences/logs.
- Respect explicit inaccessible/DRM media; no bypass mechanisms.
- Validate TLS, URLs, input types, API shapes, pagination and resumed downloads.
- Preserve the original upload. Work only inside this new artifact directory.

## Review focus
- Malformed/sanitized imports must fail clearly without disclosing secret values.
- Expired sessions, repeated pages, short pages with hasMore, and failed later pages.
- Interrupted files, ignored/malformed ranges, redirects and successful HTML responses.
- Optional dependencies and OS keychain availability cannot silently downgrade storage.
- Worker completion/cancellation must leave UI controls and manifests consistent.

## Tasks
- [x] Auth: write parser/storage tests; observe missing behavior; implement auth.py and settings.py; rerun tests.
- [x] API: write signing/pagination/retry tests; implement common.py and api.py; rerun tests.
- [x] Downloads: write filesystem/stream/resume/filter tests; implement downloads.py; rerun tests.
- [x] Interface: write UI/browser/CLI checks; implement browser.py, ui.py and cli.py; run under a virtual display.
- [x] Distribution: launch scripts, README, CHANGELOG, security notes and full verification report; ZIP and verify contents.

Commit/push steps are inapplicable: this is an uploaded-file artifact, not a Git repository.
