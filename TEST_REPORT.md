# Verification report — Desktop 2.0.3

Build date: 2026-10-06. This report covers local/offline verification of the Activity
preview and legacy-file compatibility update. It is not evidence of live platform/API
compatibility or a formal security audit.

## Results

**163 tests passed; 0 failures, 0 errors, 0 skipped** under Xvfb with a real Tk event
loop. `python -m compileall -q ofdl onlyfans-dl.py tests` also completed successfully.

| Area | Coverage |
| --- | --- |
| Existing 2.0.2 suite | Authentication/import, signing/API diagnostics, pagination, browser helper, creator scope/date controls, resumable downloads, manifests, filters and Tk behavior |
| Legacy layouts | Exact old files, old category-subfolder layout when current mode is flat, old photo-album layout when albums are disabled, and original `gifs/` layout |
| Legacy safety | Empty/`.part` files are not adopted; symlink/path containment protections remain in the existing suite |
| Manifest migration | Deferred legacy records are flushed/persisted on close; downloaded files retain normal immediate recording |
| Preview helper | Image thumbnail size/source preservation, missing-file handling, lightweight no-ffmpeg video fallback |
| Preview integration | Download completion event, no preview event for legacy adoption, actual Tk right-side preview panel and asynchronous event delivery |

The new regressions were run before implementation and failed for the expected missing
behaviors: old non-current layouts caused a media request, no media-complete event was
emitted, the preview helper/module did not exist, and the Activity panel was absent.
They pass after the implementation.

A full-suite run initially printed Tk `after` callback warnings even though every test
passed. Investigation traced those warnings to the new test manually invoking `_poll()`
while Tk's scheduled poll was still active, creating duplicate scheduled callbacks.
The test was corrected to drive the real event loop instead; the focused reproduction
then completed without warnings.

## Large-library behavior

The compatibility path is designed for libraries on the order of the reported
~80,000 files / ~125 GB:

- Existing files at the expected path are checked directly; no creator-tree scan is
  needed for those files.
- After the first exact-path miss for a creator, that creator tree is scanned once and
  indexed by media ID/type for the remainder of the run.
- Duplicate discovery reads directory metadata and filenames, not the 125 GB of file
  contents; there is no migration-time full hashing.
- Legacy manifest inserts are committed in batches of 500 and on clean close to avoid
  one SQLite commit per old file.
- Existing/legacy files never enter the preview queue. Only newly completed media does,
  and only the newest pending preview is retained.

Actual scan duration depends heavily on storage latency (especially HDD/NAS/antivirus)
and was not treated as a portable benchmark.

## Runtime used

Linux x86-64; CPython 3.13.5; Requests 2.32.5; Pillow 12.3.0; Tk 8.6; Xvfb.
`ffmpeg` was present in the build environment, but the regression for the no-ffmpeg
path explicitly simulates its absence. Browser tests use simulated responses/errors;
no live browser/account is required.

Python 3.10+ remains the source compatibility target, not an executed multi-version
matrix. Native Windows/macOS launchers and actual OS credential stores were not run.

## Explicitly not verified

No real account credentials were used. No live authenticated API request, subscription
scan, or real media download was performed for this build. Current signing rules, API
behavior, temporary media URLs and security challenges can still change independently.

Legacy files are intentionally trusted by recognized filename/media ID and non-zero
size on first adoption. That avoids reading/hashing a very large library, but it cannot
prove an old file is complete or uncorrupted. Known-bad old files should be removed and
redownloaded.

## Reproduce

From the extracted package:

```bash
python -m compileall -q ofdl onlyfans-dl.py tests
python onlyfans-dl.py --version
python onlyfans-dl.py --help
python -m unittest discover -s tests -v
```

On headless Linux with Xvfb:

```bash
xvfb-run -a python -m unittest discover -s tests -v
```

Expected complete run for this package: `Ran 163 tests` followed by `OK`.
