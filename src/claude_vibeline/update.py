import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import platformdirs
import requests

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import (
    DIM,
    GOLD,
    LABEL,
    NBSP,
    PYPI_URL,
    RESET,
    UPDATE_CHECK_INTERVAL,
    UPDATE_FETCH_TIMEOUT,
)

if TYPE_CHECKING:
    from claude_vibeline.schema import UpdateCache


def update_cache_path() -> Path:
    return Path(platformdirs.user_cache_dir('claude-vibeline')) / 'update.json'


def read_update_cache() -> UpdateCache:
    try:
        data = json.loads(update_cache_path().read_text())
    except OSError, json.JSONDecodeError:
        return {}
    if data.get('_v') != app_version:
        return {}
    data.pop('_v', None)
    return data


def write_update_cache(data: UpdateCache) -> None:
    path = update_cache_path()
    payload: dict[str, Any] = {**data, '_v': app_version}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        pass


def fetch_latest_version() -> str | None:
    try:
        resp = requests.get(PYPI_URL, timeout=UPDATE_FETCH_TIMEOUT)
        resp.raise_for_status()
        version = resp.json().get('info', {}).get('version')
    except requests.RequestException, json.JSONDecodeError, ValueError:
        return None
    return version if isinstance(version, str) else None


def _parse_version(v: str) -> tuple[int, ...] | None:
    parts: list[int] = []
    for p in v.split('.'):
        if not p.isdigit():
            return None
        parts.append(int(p))
    return tuple(parts) or None


def is_newer(latest: str, current: str) -> bool:
    a, b = _parse_version(latest), _parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def check_for_update(*, is_new_session: bool) -> str | None:
    """
    Return the latest version if newer than installed, else None.

    Triggers a PyPI fetch only on a new session and only when at least
    UPDATE_CHECK_INTERVAL has elapsed since the last check. The cached
    `latest` is still consulted on every call, so the message keeps rendering
    between fetches.
    """
    cache = read_update_cache()
    now = time.time()
    due = is_new_session and (now - cache.get('checked_ts', 0)) > UPDATE_CHECK_INTERVAL
    latest = cache.get('latest')

    if due:
        fresh = fetch_latest_version()
        update: UpdateCache = {'checked_ts': now}
        if fresh is not None:
            latest = fresh
            update['latest'] = fresh
        elif latest is not None:
            update['latest'] = latest
        write_update_cache(update)

    if latest is None:
        return None
    return latest if is_newer(latest, app_version) else None


def format_update_message(latest: str) -> str:
    label = f'update{NBSP}available'
    version_range = f'{app_version}{NBSP}\u2192{NBSP}{latest}'
    return (
        f'{LABEL}{label}{RESET}{DIM}:{RESET} '
        f'{GOLD}{version_range}{RESET}{NBSP}{DIM}\u00b7{RESET} '
        f'{DIM}uv{NBSP}tool{NBSP}upgrade{NBSP}claude-vibeline{RESET}'
    )
