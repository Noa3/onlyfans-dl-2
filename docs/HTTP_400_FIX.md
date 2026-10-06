# Desktop 2.0.1 — HTTP 400 diagnosis and corrective patch

## What the supplied log establishes

The helper captured values, and subsequent desktop session tests received HTTP 400.
There were also separate browser-launch and browser-interruption failures. A rules
refresh was reported as schema-valid; that does not establish server acceptance.
The screenshot exposed a session cookie. Those values were not used for any request,
fixture, test, artifact or credential-store write during this repair.

## What was confirmed in the code

In Desktop 2.0.0, `ApiClient.request()` computed a signature with `auth.user_id`,
then removed every header named in the public rules' `remove_headers` list. The
selected DATAHOARDERS file contained `"remove_headers": ["user-id"]` when inspected.
The emitted request therefore lacked the identity header the authenticated client
had used to sign it. Rules metadata for other client implementations cannot safely
change that contract halfway through constructing this client's request.

2.0.1 preserves `user-id`, applies permitted optional removals before signing, and
uses `prepared.headers['user-id']` in the signature. It also preserves the distinction
between underscore and hyphen in literal HTTP header names.

The old `test_rule_header_removal_after_signing` asserted that deletion was correct.
It has been replaced. A new independent test adapter recomputes the signature from
the transmitted header and path; it rejects the old request and accepts the corrected
one. This is a **simulated endpoint enforcing a client contract**, not a live OnlyFans
acceptance test. The millisecond timestamp and exact prepared-query signing were
already present in 2.0.0 and were not newly fixed by this patch.

This missing-header defect is a plausible contributor to the reported HTTP 400,
**not a proven or exclusive live root cause**. The old log contains no server error
body; rule freshness, browser-session transfer, website protections or a changed
endpoint may still require further work. There is no automatic fallback to another
rule provider and no hardcoded current ruleset.

## Diagnostics added for the next test

A rejected request produces one `Safe diagnostic:` line with:

- `stage=api`, HTTP status, response type and bounded-body parsing status.
- An allowlisted message category (`invalid-signature`, `refresh-required`,
  `authentication-rejected`, `access-denied`, `rate-limited`, or `unknown`). Categories
  reflect recognized messages only; numeric codes are not treated as proof of a cause.
- A small integer `error.code` from a JSON error object, when present. Other code
  shapes and values are discarded. Raw server messages are never printed.
- Whether the outgoing identity header matches the signed identity, the timestamp
  unit, and a 12-character SHA-256 identifier of **public rule content only**.
- A coarse comparison with the server's Date header: unknown, within five minutes,
  local-ahead, or local-behind. A server/proxy Date header is only an observation,
  not an authoritative clock service. The application does not change your clock.

Error bodies are bounded to 16 KiB plus one 4 KiB read chunk. Oversized, unreadable
or HTML responses retain the original status. Response bodies/headers, cookies,
account IDs, user-agent strings, request URLs, and raw browser exception text are
not included in these generated diagnostics. HTTP 400 is not blindly retried.
A JSON error object delivered with HTTP 200 is still reported as a failure.

Browser errors have a separate `browser-launch` / `browser-capture` stage and safe
category. Missing browser installations and interrupted/closed browsers should not
be mistaken for a rejected API session. Raw errors remain withheld.

## Browser and UI corrections

Browser capture now waits for `requestfinished`, then verifies an HTTP 200 JSON
response from `/api2/v2/users/me` identifying the same account as the request headers.
It does not accept a public API response, HTML response or another account's profile
as proof of capture. It reads no login-password or two-factor POST bodies. Browser
success and successful reuse by the desktop Requests client are distinct checks.
A browser/context cleanup error no longer erases an already captured result.

The desktop account test also checks the returned ID. Fresh imports and tests hide
private values again. Failed/stopped tests say not ready, and edited fields invalidate
previous success. A delayed test callback cannot certify changed inputs.

## Upgrade and test

Revoke the exposed website session first. Close 2.0.0, extract the complete 2.0.1 ZIP
into a new folder, run the setup and start scripts there, and confirm the version in
the title. Keep the same download output folder; no deletion of existing media is
required. Fresh-folder browser login needs its optional helper installation again;
request import does not.

Log in normally, import a new request locally or use Browser login, refresh rules
once, and run Test session. If accepted, try Scan only before downloads. If rejected,
share only the generated Safe diagnostic line, not cookies, copied cURL, HAR files,
account IDs, unmasked screenshots or the full media log. There is no need to disclose
a real session to debug these diagnostics.

## Evidence and source distinction

The package code, failing-then-passing local regressions, and supplied log are the
basis for this fix. The following primary project/library sources were reviewed on
2026-10-06 as cross-checks, not as official guarantees of platform behavior:

- DATAHOARDERS rules file: https://github.com/DATAHOARDERS/dynamic-rules/blob/main/onlyfans.json
  GitHub blob observed: `6f02457aaf8f8289db4ec8cbc55e2aaea2234323`. It contains the
  literal `user-id` removal entry. Nothing in this patch assumes its current signing
  values are accepted by the service.
- OF-Scraper authenticated headers/signing: https://github.com/datawhores/OF-Scraper/blob/main/ofscraper/utils/auth/request.py
  GitHub blob observed: `9b71705a09ac75bf7c96006a2f952af76a8208b7`. Its login client
  retains `user-id` and signs with the ID read from that header. This is a separate
  client implementation, not an official OnlyFans API specification.
- Playwright completed-request event: https://playwright.dev/python/docs/api/class-browsercontext#browser-context-event-request-finished
- Playwright response JSON: https://playwright.dev/python/docs/api/class-response#response-json
- Playwright complete request headers: https://playwright.dev/python/docs/api/class-request#request-all-headers

See ../TEST_REPORT.md for executed checks and the important live-testing limitations.
