import contextlib
import json
import os
import subprocess as sp
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import platformdirs

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import PROMPT_CACHE_TTL

REFRESH_INTERVAL = 30


def refresh_lock_path() -> Path:
    return Path(platformdirs.user_cache_dir('claude-vibeline')) / 'refresh.lock'


def toggle_settings_space() -> None:
    """
    Toggle a trailing space in the statusLine command value in settings.json.
    """
    settings = Path('~/.claude/settings.json').expanduser()
    try:
        data = json.loads(settings.read_text(encoding='utf-8'))
    except OSError, json.JSONDecodeError:
        return
    status_line = data.get('statusLine')
    if not isinstance(status_line, dict):
        return
    cmd = status_line.get('command')
    if not isinstance(cmd, str):
        return
    status_line['command'] = cmd[:-1] if cmd.endswith(' ') else cmd + ' '
    with contextlib.suppress(OSError):
        tmp = settings.with_suffix(f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        tmp.replace(settings)


def is_lock_owner(lock: Path, token: str) -> bool:
    """
    Check if the given token still owns the lock file.
    """
    try:
        data = json.loads(lock.read_text(encoding='utf-8'))
        if data.get('_v') != app_version:
            return False
        return data.get('token') == token
    except OSError, json.JSONDecodeError, ValueError:
        return False


def run_refresh_loop(expiry_ts: float, token: str) -> None:
    """
    Background loop: toggle settings.json every REFRESH_INTERVAL seconds.

    Exits cooperatively when the lock token changes (newer updater spawned).
    The last in-loop toggle fires after sleeping until expiry, which triggers
    the re-render showing the expired cache state.
    """
    lock = refresh_lock_path()
    try:
        while True:
            if not is_lock_owner(lock, token):
                return
            remaining = expiry_ts - time.time()
            if remaining <= 0:
                break
            time.sleep(min(REFRESH_INTERVAL, remaining))
            toggle_settings_space()
    finally:
        if is_lock_owner(lock, token):
            with contextlib.suppress(OSError):
                lock.unlink()


def spawn_cache_updater(expiry_ts: float) -> None:
    """
    Spawn a detached background process that periodically toggles settings.json.

    Skips if an updater with the same or later expiry is already running.
    Spawns a new one if the expiry is later (new user message extended the cache).
    """
    lock = refresh_lock_path()
    try:
        data = json.loads(lock.read_text(encoding='utf-8'))
        if data.get('_v') == app_version and expiry_ts <= data.get('expiry', 0):
            return
    except OSError, json.JSONDecodeError, ValueError:
        pass
    token = uuid.uuid4().hex
    lock.parent.mkdir(parents=True, exist_ok=True)
    tmp = lock.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({'token': token, 'expiry': expiry_ts, '_v': app_version}))
    tmp.replace(lock)
    cmd = [
        sys.executable,
        '-c',
        f'from claude_vibeline.refresh import run_refresh_loop; run_refresh_loop({expiry_ts}, {token!r})',
    ]
    kwargs: dict[str, Any] = {'stdin': sp.DEVNULL, 'stdout': sp.DEVNULL, 'stderr': sp.DEVNULL}
    if sys.platform == 'win32':
        kwargs['creationflags'] = sp.CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True
    try:
        sp.Popen(cmd, **kwargs)
    except OSError:
        with contextlib.suppress(OSError):
            lock.unlink()
        return


def maybe_spawn_cache_updater(last_user_ts: float | None) -> None:
    if last_user_ts is None:
        return
    expiry = last_user_ts + PROMPT_CACHE_TTL
    if time.time() < expiry:
        spawn_cache_updater(expiry)
