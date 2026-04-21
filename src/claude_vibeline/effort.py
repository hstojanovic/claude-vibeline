import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import platformdirs

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import ANSI_RE, TAIL_CHUNK

if TYPE_CHECKING:
    from claude_vibeline.schema import SessionCache

EFFORT_LEVELS = r'low|medium|high|xhigh|max'
MODEL_EFFORT_RE = re.compile(rf'with ({EFFORT_LEVELS}) effort')
EFFORT_COMMAND_RE = re.compile(rf'Set effort level to ({EFFORT_LEVELS})')
SET_MODEL_PREFIX = 'Set model to'
SET_EFFORT_PREFIX = 'Set effort level to'
EFFORT_AUTO_PREFIX = 'Effort level set to auto'
CACHE_MAX_AGE = 30 * 86400  # 30 days

SUPPORTED_EFFORTS: dict[str, set[str]] = {
    'opus 4.7': {'low', 'medium', 'high', 'xhigh', 'max'},
    'opus 4.6': {'low', 'medium', 'high', 'max'},
    'sonnet 4.6': {'low', 'medium', 'high'},
}


def supported_efforts_for(model_name: str) -> set[str] | None:
    name = model_name.lower()
    return next((v for k, v in SUPPORTED_EFFORTS.items() if name.startswith(k)), None)


def read_settings_effort() -> str:
    """
    Read effort from settings.json.

    Returns the plain value; callers append '?' when the value is a best-guess
    fallback in a genuinely uncertain context.
    """
    try:
        settings = Path('~/.claude/settings.json').expanduser()
        effort = json.loads(settings.read_text()).get('effortLevel', 'medium')
    except OSError, json.JSONDecodeError:
        effort = 'medium'
    return effort


def parse_effort_from_line(visible: str) -> str | None:
    """
    Extract effort from a visible (ANSI-stripped) transcript line.

    Matches three formats:
      /model:  "Set model to ... with {effort} effort"
      /effort: "Set effort level to {effort}"
      /effort auto: "Effort level set to auto" -> returns 'auto'
    """
    if SET_MODEL_PREFIX in visible:
        match = MODEL_EFFORT_RE.search(visible)
        return match.group(1) if match is not None else None
    if SET_EFFORT_PREFIX in visible:
        match = EFFORT_COMMAND_RE.search(visible)
        if match is not None:
            return match.group(1)
    if EFFORT_AUTO_PREFIX in visible:
        return 'auto'
    return None


# --- Session cache ---


def session_cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir('claude-vibeline')) / 'sessions'


def read_session_cache(session_id: str) -> SessionCache:
    try:
        cache_file = session_cache_dir() / f'{session_id}.json'
        data = json.loads(cache_file.read_text())
    except OSError, json.JSONDecodeError:
        return {}
    if data.get('_v') != app_version:
        return {}
    return data


def write_session_cache(session_id: str, data: SessionCache) -> None:
    """
    Merge data into the session cache file (read-modify-write).
    """
    cache_dir = session_cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f'{session_id}.json'
        is_new = not cache_file.exists()
        existing = read_session_cache(session_id)
        merged = {**existing, **data, '_v': app_version}
        tmp = cache_file.with_suffix(f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(merged))
        tmp.replace(cache_file)
        if is_new:
            cleanup_session_cache(cache_dir)
            (cache_dir.parent / 'refresh.lock').unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_session_cache(cache_dir: Path) -> None:
    try:
        cutoff = time.time() - CACHE_MAX_AGE
        for f in cache_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
    except OSError:
        pass


# --- Effort scanning ---


class EffortScanner:
    def __init__(self, since_ts: str) -> None:
        self.since_ts = since_ts
        self.saw_synthetic: bool = False
        self.latest_ts: str = ''
        self.effort: str | None = None
        self.done: bool = False

    def process_entry(self, entry: dict[str, Any]) -> None:
        ts = entry.get('timestamp', '')
        if ts and self.since_ts and ts <= self.since_ts:
            self.done = True
            return
        if ts and ts > self.latest_ts:
            self.latest_ts = ts

        msg = entry.get('message', {})
        if msg.get('model') == '<synthetic>':
            content = msg.get('content', [])
            if isinstance(content, list) and any(
                b.get('text') == 'No response requested.' for b in content if isinstance(b, dict)
            ):
                self.saw_synthetic = True
            return

        content = msg.get('content', '')
        if not isinstance(content, str):
            return
        visible = ANSI_RE.sub('', content)
        effort = parse_effort_from_line(visible)
        if effort is not None:
            self.effort = None if self.saw_synthetic else ('medium' if effort == 'auto' else effort)
            self.done = True


def scan_transcript_effort(transcript_path: str | None, since_ts: str = '') -> tuple[str | None, str, bool]:
    """
    Scan transcript backwards for the last effort-setting command.

    Returns (effort_or_none, latest_timestamp, saw_synthetic).
    Effort is invalidated if a <synthetic> "No response requested." entry
    appears more recently (indicates session resume/exit).
    """
    if transcript_path is None:
        return None, '', False
    scanner = EffortScanner(since_ts)
    try:
        with Path(transcript_path).open('rb') as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None, '', False
            read_total = 0
            while read_total < size:
                read_total = min(size, read_total + TAIL_CHUNK)
                f.seek(-read_total, 2)
                tail = f.read(read_total).decode('utf-8', errors='replace')
                for line in reversed(tail.splitlines()):
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    scanner.process_entry(entry)
                    if scanner.done:
                        return scanner.effort, scanner.latest_ts, scanner.saw_synthetic
    except OSError:
        pass
    return scanner.effort, scanner.latest_ts, scanner.saw_synthetic


def resolve_effort(transcript_path: str | None, session_id: str | None) -> str:
    """
    Resolve effort: transcript -> session cache -> settings fallback.
    """
    cached = read_session_cache(session_id) if session_id is not None else {}
    effort, latest_ts, saw_synthetic = scan_transcript_effort(transcript_path, cached.get('effort_ts', ''))

    if effort is not None:
        if session_id is not None:
            write_session_cache(session_id, {'effort': effort, 'effort_ts': latest_ts})
        return effort

    if session_id is not None and latest_ts:
        update: SessionCache = {'effort_ts': latest_ts}
        if saw_synthetic:
            # Resumed session: settings value is a best guess; mark uncertain.
            fallback = f'{read_settings_effort()}?'
            write_session_cache(session_id, {**update, 'effort': fallback})
            return fallback
        write_session_cache(session_id, update)

    effort = cached.get('effort') or None
    if effort is not None:
        return effort

    fallback = read_settings_effort()
    if session_id is not None:
        write_session_cache(session_id, {'effort': fallback})
    return fallback


def refine_effort_for_model(effort: str, model_name: str) -> str:
    """
    Fall back to settings if the transcript effort isn't supported on this model.

    Unknown models and already-fallback efforts pass through unchanged so
    model_section can apply its own xhigh → high degrade.
    """
    supported = supported_efforts_for(model_name)
    if supported is None or effort.rstrip('?') in supported or effort.endswith('?'):
        return effort
    return f'{read_settings_effort()}?'
