"""Nonsecret preferences and atomic, restricted-permission JSON files."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_RULES_SOURCE = 'https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json'
PREFERENCE_KEYS = {'output_dir','profiles','skip_profiles','days','since','albums','subfolders',
                   'photos','videos','audio','posts','stories','messages','archived','purchased',
                   'previews','rules_source','browser_channel','creator_mode','date_mode'}


def config_dir() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'OnlyFansDL'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'OnlyFansDL'
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'onlyfans-dl'


def atomic_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temp = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_preferences(values: dict[str, Any], path: Path | None = None) -> None:
    safe = {key:value for key,value in values.items() if key in PREFERENCE_KEYS and isinstance(value, (str,int,bool))}
    atomic_json(path or config_dir() / 'settings.json', safe)


def load_preferences(path: Path | None = None) -> dict[str, Any]:
    path = path or config_dir() / 'settings.json'
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError('too large')
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, dict):
            raise ValueError('not an object')
        return {key:value for key,value in values.items() if key in PREFERENCE_KEYS and isinstance(value,(str,int,bool))}
    except (OSError, ValueError, RecursionError):
        raise ValueError('Saved preferences could not be read. Rename settings.json in the application settings folder to reset them.') from None
