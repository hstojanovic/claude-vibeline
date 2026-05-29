import json
import os
import subprocess as sp
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import platformdirs
import requests

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import CACHE_TTL_SECONDS, USAGE_URL

if TYPE_CHECKING:
    from claude_vibeline.schema import OAuthCredentials, OAuthEntry, UsageData


def token_from_entry(entry: OAuthEntry) -> str | None:
    token = entry.get('accessToken')
    if token is None:
        return None
    expires_at = entry.get('expiresAt')
    if expires_at is not None and time.time() >= expires_at:
        return None
    return token


def read_oauth_token() -> str | None:
    creds_path = Path('~/.claude/.credentials.json').expanduser()
    try:
        creds: OAuthCredentials = json.loads(creds_path.read_text())
        token = token_from_entry(creds.get('claudeAiOauth', {}))
        if token is not None:
            return token
    except OSError, json.JSONDecodeError:
        pass

    # macOS Keychain fallback
    if sys.platform == 'darwin':
        try:
            result = sp.run(
                ['/usr/bin/security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
                capture_output=True,
                text=True,
                check=True,
            )
            keychain_data: OAuthCredentials = json.loads(result.stdout.strip())
            return token_from_entry(keychain_data.get('claudeAiOauth', {}))
        except sp.CalledProcessError, OSError, json.JSONDecodeError:
            pass

    return None


def cache_path() -> Path:
    return Path(platformdirs.user_cache_dir('claude-vibeline')) / 'usage.json'


def fetch_usage() -> tuple[UsageData | None, float | None]:
    """
    Return (usage, stale_ts) from the OAuth API, falling back to cache.

    `_ts` is the time of the last *successful* fetch and is only ever advanced
    by a success. Within CACHE_TTL_SECONDS of it the cached data is served as
    current; past that a refresh is attempted, and on failure the cached data is
    served with its real age via `stale_ts` so the staleness marker reflects how
    old the data actually is. Because failures never touch the cache, the marker
    doesn't flicker off between renders and a credential written mid-session is
    picked up on the next render.
    """
    cache = cache_path()
    stale: UsageData | None = None
    data_ts: float = 0
    try:
        if cache.exists():
            cached = json.loads(cache.read_text())
            if cached.get('_v') != app_version:
                cache.unlink(missing_ok=True)
            else:
                data_ts = cached.pop('_ts', 0)
                cached.pop('_v', None)
                stale = cached or None
                if stale is not None and time.time() - data_ts < CACHE_TTL_SECONDS:
                    return stale, None
    except OSError, json.JSONDecodeError:
        pass

    marker = data_ts if stale is not None else None

    token = read_oauth_token()
    if token is None:
        return stale, marker

    try:
        resp = requests.get(
            USAGE_URL, headers={'Authorization': f'Bearer {token}', 'anthropic-beta': 'oauth-2025-04-20'}, timeout=3
        )
        resp.raise_for_status()
        data: UsageData = resp.json()
    except requests.RequestException, json.JSONDecodeError:
        return stale, marker

    write_usage_cache(cache, data)
    return data, None


def write_usage_cache(cache: Path, data: UsageData | None) -> None:
    payload: dict[str, Any] = {**(data or {}), '_ts': time.time(), '_v': app_version}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(payload))
        tmp.replace(cache)
    except OSError:
        pass
