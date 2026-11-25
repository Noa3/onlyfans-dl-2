"""Turn a user-entered output folder into a local filesystem path.

The desktop UI accepts ordinary paths. Browser-style URLs such as
``smb://nas/share`` are not filesystem paths, so on KDE they are resolved to a
local KIO-FUSE mount before any file operation. Other URL schemes are rejected
with a message that names the fix instead of a generic OSError.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .common import AppError

_SCHEME = re.compile(r'^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]{1,}):/*(?P<rest>.*)$')
_REMOTE_SCHEMES = {'smb', 'sftp', 'ftp', 'ftps', 'nfs', 'dav', 'davs', 'webdav', 'ssh', 'fish'}
_WEB_SCHEMES = {'http', 'https'}
_KIOFUSE_METHOD = 'org.kde.KIOFuse.VFS.mountUrl'


def _kio_fuse(url: str) -> Path | None:
    """Ask KIO-FUSE (KDE) for a local path; return None when unavailable."""
    if sys.platform == 'win32':
        return None
    executable = shutil.which('gdbus')
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, 'call', '--session', '--dest', 'org.kde.KIOFuse',
             '--object-path', '/org/kde/KIOFuse', '--method', _KIOFUSE_METHOD, url],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        value = ast.literal_eval(completed.stdout.strip())
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, tuple) and len(value) == 1 and isinstance(value[0], str) and value[0]:
        return Path(value[0])
    return None


def local_output_path(value: object) -> Path:
    """Return a writable-local path for a folder the user typed or picked."""
    raw = str(value).strip()
    if not raw:
        raise AppError('Choose an output folder.')
    match = _SCHEME.fullmatch(raw)
    if match:
        scheme = match.group('scheme').lower()
        if scheme == 'file':
            parsed = urlsplit(raw)
            if parsed.netloc not in {'', 'localhost'}:
                raise AppError('Only local file:// paths are supported. Choose a local folder.')
            return Path(unquote(parsed.path)).expanduser().resolve()
        if scheme in _WEB_SCHEMES:
            raise AppError('The output folder must be a local folder, not a web address. '
                           'Mount the network share and choose the mounted folder.')
        if scheme in _REMOTE_SCHEMES:
            # Path() collapses "smb://" to "smb:/" before validation, so rebuild it.
            resolved = _kio_fuse(f'{scheme}://{match.group("rest")}')
            if resolved is None:
                raise AppError(
                    'The output folder is a network URL, which cannot be written to directly. '
                    'Mount the share in your file manager or operating system and choose the '
                    'mounted folder, or pick a local folder such as ~/Downloads/OnlyFans.')
            return resolved
        if '://' in raw:
            raise AppError('The output folder must be a local filesystem path, not a URL. '
                           'Mount the network share and choose the mounted folder.')
    return Path(raw).expanduser().resolve()
