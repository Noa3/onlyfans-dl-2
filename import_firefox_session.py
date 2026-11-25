#!/usr/bin/env python3
"""Import the logged-in onlyfans.com session from a local Firefox profile.

The desktop helper normally opens a fresh browser. This script instead reuses
the session you already have in Firefox:

  * ``sess`` / ``auth_id`` cookies from ``cookies.sqlite``
  * the ``bcTokenSha`` value from the site's localStorage (the ``x-bc`` header)
  * a user agent derived from that profile's Firefox version

Files are copied to a temporary folder and read there, so a running Firefox is
fine. Secret values are never printed, logged, or written to disk; the result is
stored in the OS credential store with the same key the GUI's "Load saved
session" button uses.

Use only your own account. Run ``--dry-run`` first if you want to check that the
values can be found without saving anything.
"""
from __future__ import annotations

import argparse
import configparser
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from ofdl.auth import Credentials, SessionError, save_session


def _profile_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / '.mozilla' / 'firefox',
        Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'mozilla' / 'firefox',
        home / '.var' / 'app' / 'org.mozilla.firefox' / '.mozilla' / 'firefox',
        home / 'snap' / 'firefox' / 'common' / '.mozilla' / 'firefox',
    ]
    roots: list[Path] = []
    for path in candidates:
        if path not in roots and (path / 'profiles.ini').is_file():
            roots.append(path)
    return roots


def _profiles(root: Path) -> list[Path]:
    """Return every profile directory listed in profiles.ini, then any stray one."""
    found: list[Path] = []
    parser = configparser.ConfigParser()
    try:
        parser.read(root / 'profiles.ini', encoding='utf-8')
    except (configparser.Error, OSError):
        parser = configparser.ConfigParser()
    for section in parser.sections():
        if not section.lower().startswith('profile'):
            continue
        raw = parser.get(section, 'Path', fallback='').strip()
        if not raw:
            continue
        relative = parser.getboolean(section, 'IsRelative', fallback=True)
        path = (root / raw) if relative else Path(raw)
        if path.is_dir() and path not in found:
            found.append(path)
    for candidate in sorted(root.iterdir()):
        if candidate.is_dir() and (candidate / 'cookies.sqlite').is_file() and candidate not in found:
            found.append(candidate)
    return found


def _copy_database(source: Path, folder: Path) -> Path:
    target = folder / source.name
    shutil.copy2(source, target)
    for suffix in ('-wal', '-shm'):
        companion = Path(str(source) + suffix)
        if companion.is_file():
            shutil.copy2(companion, str(target) + suffix)
    return target


def _cookies(profile: Path, folder: Path) -> dict[str, str]:
    database = _copy_database(profile / 'cookies.sqlite', folder)
    try:
        connection = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
        try:
            rows = connection.execute(
                "select name, value from moz_cookies "
                "where host in ('onlyfans.com', '.onlyfans.com')").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SessionError(f'cookies.sqlite could not be read: {exc}') from None
    cookies: dict[str, str] = {}
    for name, value in rows:
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if value[:3] in {'v10', 'v11'}:
            raise SessionError(
                'This Firefox profile stores encrypted cookies (master password or an OS key store). '
                'Remove the Firefox master password or use the browser helper instead.')
        cookies[name] = value
    return cookies


def _bc_token(profile: Path, folder: Path) -> str:
    database = profile / 'storage' / 'default' / 'https+++onlyfans.com' / 'ls' / 'data.sqlite'
    if not database.is_file():
        raise SessionError('The Firefox profile has no onlyfans.com localStorage. Open onlyfans.com in Firefox once, then retry.')
    copy = _copy_database(database, folder)
    try:
        connection = sqlite3.connect(f'file:{copy}?mode=ro', uri=True)
        try:
            row = connection.execute(
                "select value, conversion_type, compression_type from data where key='bcTokenSha'").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SessionError(f'Firefox localStorage could not be read: {exc}') from None
    if row is None:
        raise SessionError('bcTokenSha was not found. Open onlyfans.com while logged in, reload the page, then retry.')
    blob, conversion, compression = row
    if compression:
        raise SessionError('The stored bcTokenSha is compressed; open onlyfans.com in Firefox and retry.')
    encoding = 'utf-8' if conversion == 1 else 'utf-16-le'
    try:
        token = bytes(blob).decode(encoding)
    except (TypeError, UnicodeDecodeError):
        raise SessionError('The stored bcTokenSha could not be decoded.') from None
    token = token.strip()
    if not re.fullmatch(r'[0-9A-Za-z]{20,128}', token):
        raise SessionError('The stored bcTokenSha does not look like an x-bc value.')
    return token


def _user_agent(profile: Path) -> str:
    parser = configparser.ConfigParser()
    try:
        parser.read(profile / 'compatibility.ini', encoding='utf-8')
        version = parser.get('Compatibility', 'LastVersion', fallback='')
        abi = parser.get('Compatibility', 'LastOSABI', fallback='')
    except (configparser.Error, OSError):
        version, abi = '', ''
    major = version.split('.', 1)[0]
    if not major.isdigit():
        raise SessionError('The Firefox version could not be read; start Firefox once, then retry.')
    machine = 'aarch64' if 'aarch64' in abi.lower() or 'arm64' in abi.lower() else 'x86_64'
    return (f'Mozilla/5.0 (X11; Linux {machine}; rv:{major}.0) '
            f'Gecko/20100101 Firefox/{major}.0')


def build(profile: Path, folder: Path) -> Credentials:
    cookies = _cookies(profile, folder)
    sess = cookies.get('sess', '')
    user_id = cookies.get('auth_id', '')
    if not sess or not user_id:
        raise SessionError('No logged-in onlyfans.com sess/auth_id cookie was found. Log into onlyfans.com in Firefox, then retry.')
    extras = {name: cookies[name] for name in ('auth_id',) if name in cookies}
    auth = Credentials(user_id, _user_agent(profile), _bc_token(profile, folder), sess, extras)
    auth.validate()
    return auth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Import the current Firefox onlyfans.com session into the OS credential store.')
    parser.add_argument('--dry-run', action='store_true', help='Only check that a session can be read; do not save it.')
    parser.add_argument('--profile', type=Path, help='Explicit Firefox profile directory.')
    args = parser.parse_args(argv)

    profiles = [args.profile] if args.profile else [p for root in _profile_roots() for p in _profiles(root)]
    if not profiles:
        raise SystemExit('No Firefox profile was found. Start Firefox once, or pass --profile.')

    errors: list[str] = []
    for profile in profiles:
        if not (profile / 'cookies.sqlite').is_file():
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='ofdl-firefox-') as temp:
                auth = build(profile, Path(temp))
        except SessionError as exc:
            errors.append(f'{profile.name}: {exc}')
            continue
        print(f'Firefox profile: {profile}')
        print(f'Found cookies: sess, auth_id (user {auth.user_id}), x-bc from localStorage.')
        print(f'User agent: {auth.user_agent}')
        if args.dry_run:
            print('Dry run: nothing was saved.')
            return 0
        save_session(auth)
        print('Saved to the OS credential store. Use "Load saved session" in the GUI, or --use-keyring in the CLI.')
        return 0

    print('No usable onlyfans.com session was found in these Firefox profiles:', file=sys.stderr)
    for message in errors:
        print(f'  {message}', file=sys.stderr)
    print('Log into onlyfans.com in Firefox, reload the page, then run this script again.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SessionError as exc:
        raise SystemExit(str(exc)) from None
