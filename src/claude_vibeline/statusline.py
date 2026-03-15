import contextlib
import dataclasses
import io
import json
import os
import re
import subprocess as sp
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, TypedDict

import cappa
import platformdirs
import requests

from claude_vibeline import __doc__ as description, __version__ as app_version

# --- Colors (Claude palette) ---
RESET = '\033[0m'
ORANGE = '\033[38;5;209m'
CREAM = '\033[1;38;5;222m'
GOLD = '\033[38;5;180m'
LABEL = '\033[38;5;137m'
DIM = '\033[38;5;240m'
BAR_EMPTY = '\033[38;5;238m'
PERC = '\033[38;5;222m'
GREEN = '\033[38;5;114m'
YELLOW = '\033[38;5;179m'
RED = '\033[38;5;167m'

FILL = '\u2588'
EMPTY = '\u2591'
NBSP = '\u00a0'
SEP = f'{NBSP}{DIM}\u2502{RESET} '
ANSI_RE = re.compile(r'\033\[[0-9;]*m')

USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
CACHE_TTL_SECONDS = 60
PROMPT_CACHE_TTL = 300  # 5-minute prompt cache TTL
CACHE_LOW_THRESHOLD = 120  # 2 minutes — switch from green to yellow
DEBUG_LOG_MAX_BYTES = 1_000_000
EFFORT_LEVELS = r'low|medium|high|max'
MODEL_EFFORT_RE = re.compile(rf'with ({EFFORT_LEVELS}) effort')
EFFORT_COMMAND_RE = re.compile(rf'Set effort level to ({EFFORT_LEVELS})')
SET_MODEL_PREFIX = 'Set model to'
SET_EFFORT_PREFIX = 'Set effort level to'
EFFORT_AUTO_PREFIX = 'Effort level set to auto'


class UsageBucket(TypedDict, total=False):
    utilization: int | None
    resets_at: str


class ExtraUsage(TypedDict, total=False):
    is_enabled: bool
    used_credits: int
    monthly_limit: int


class UsageData(TypedDict, total=False):
    five_hour: UsageBucket
    seven_day: UsageBucket
    seven_day_opus: UsageBucket
    seven_day_sonnet: UsageBucket
    extra_usage: ExtraUsage


class OAuthEntry(TypedDict, total=False):
    accessToken: str
    refreshToken: str
    expiresAt: int


class OAuthCredentials(TypedDict, total=False):
    claudeAiOauth: OAuthEntry


class Workspace(TypedDict, total=False):
    project_dir: str
    current_dir: str


class Model(TypedDict, total=False):
    display_name: str


class ContextWindow(TypedDict, total=False):
    used_percentage: float
    context_window_size: int


class StdinData(TypedDict, total=False):
    workspace: Workspace
    model: Model
    context_window: ContextWindow
    transcript_path: str
    session_id: str


@dataclasses.dataclass
@cappa.command(name='claude-vibeline', description=description)
class Args:
    columns: Annotated[int, cappa.Arg(long='--columns', help='terminal width in characters')] = 80
    bar_width: Annotated[int, cappa.Arg(long='--bar-width', help='progress bar width in characters')] = 8
    currency: Annotated[str, cappa.Arg(long='--currency', help='currency symbol for extra usage')] = '€'
    project: Annotated[bool, cappa.Arg(long='--no-project', help='hide project name', show_default=False)] = True
    model: Annotated[bool, cappa.Arg(long='--no-model', help='hide model and effort level', show_default=False)] = True
    cache: Annotated[bool, cappa.Arg(long='--no-cache', help='hide prompt cache status', show_default=False)] = True
    refresh: Annotated[
        bool, cappa.Arg(long='--no-refresh', help='disable background cache timer refresh', show_default=False)
    ] = True
    context: Annotated[
        bool, cappa.Arg(long=['--no-context', '--no-ctx'], help='hide context window usage', show_default=False)
    ] = True
    usage: Annotated[
        bool, cappa.Arg(long='--no-usage', help='skip fetching usage data entirely', show_default=False)
    ] = True
    session: Annotated[
        bool,
        cappa.Arg(long=['--no-session', '--no-sess', '--no-5h'], help='hide session (5h) usage', show_default=False),
    ] = True
    weekly: Annotated[
        bool, cappa.Arg(long=['--no-weekly', '--no-week', '--no-7d'], help='hide weekly (7d) usage', show_default=False)
    ] = True
    opus: Annotated[bool, cappa.Arg(long='--no-opus', help='hide weekly Opus usage', show_default=False)] = True
    sonnet: Annotated[bool, cappa.Arg(long='--no-sonnet', help='hide weekly Sonnet usage', show_default=False)] = True
    extra: Annotated[bool, cappa.Arg(long='--no-extra', help='hide extra usage spend', show_default=False)] = True
    debug: Annotated[bool, cappa.Arg(long='--debug', help='log each output to debug file', show_default=False)] = False

    version: Annotated[
        str,
        cappa.Arg(
            app_version,
            short='-v',
            long='--version',
            action=cappa.ArgAction.version,
            help='Show version and exit.',
            group=cappa.Group(name='Help', section=2),
        ),
    ] = app_version


def bar(perc: int, width: int) -> str:
    width = max(0, width)
    if not width:
        return ''
    perc = max(0, min(100, perc))
    filled = round(perc * width / 100)
    empty = width - filled
    return f'{ORANGE}{FILL * filled}{BAR_EMPTY}{EMPTY * empty}{RESET}'


# --- Effort resolution (not in stdin, parsed from transcript) ---


def read_settings_effort() -> str:
    """
    Read effort from settings.json, suffixed with '?' to indicate uncertainty.
    """
    try:
        settings = Path('~/.claude/settings.json').expanduser()
        effort = json.loads(settings.read_text()).get('effortLevel', 'medium')
    except OSError, json.JSONDecodeError:
        effort = 'medium'
    return f'{effort}?'


def parse_effort_from_line(visible: str) -> str | None:
    """
    Extract effort from a visible (ANSI-stripped) transcript line.

    Matches three formats:
      /model:  "Set model to ... with {effort} effort"
      /effort: "Set effort level to {effort}"
      /effort auto: "Effort level set to auto" → returns 'auto'
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


class SessionCache(TypedDict, total=False):
    effort: str
    effort_ts: str
    last_user_ts: float


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
        existing = read_session_cache(session_id)
        merged = {**existing, **data, '_v': app_version}
        tmp = cache_file.with_suffix(f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(merged))
        tmp.replace(cache_file)
        cleanup_session_cache(cache_dir)
    except OSError:
        pass


CACHE_MAX_AGE = 30 * 86400  # 30 days


def cleanup_session_cache(cache_dir: Path) -> None:
    try:
        cutoff = time.time() - CACHE_MAX_AGE
        for f in cache_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
    except OSError:
        pass


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
    Resolve effort: transcript → session cache → settings fallback.
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
            fallback = read_settings_effort()
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


# --- OAuth / Usage API ---


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


def debug_log_path() -> Path:
    return Path(platformdirs.user_log_dir('claude-vibeline')) / 'debug.log'


def fetch_usage() -> tuple[UsageData | None, float | None]:
    cache = cache_path()
    stale: UsageData | None = None
    stale_ts: float = 0
    try:
        if cache.exists():
            cached = json.loads(cache.read_text())
            if cached.get('_v') != app_version:
                cache.unlink(missing_ok=True)
            else:
                stale_ts = cached.pop('_ts', 0)
                cached.pop('_v', None)
                stale = cached or None
                if time.time() - stale_ts < CACHE_TTL_SECONDS:
                    return stale, None
    except OSError, json.JSONDecodeError:
        pass

    token = read_oauth_token()
    if token is None:
        write_usage_cache(cache, stale)
        return stale, stale_ts if stale is not None else None

    try:
        resp = requests.get(
            USAGE_URL, headers={'Authorization': f'Bearer {token}', 'anthropic-beta': 'oauth-2025-04-20'}, timeout=3
        )
        resp.raise_for_status()
        data: UsageData = resp.json()
    except requests.RequestException, json.JSONDecodeError:
        write_usage_cache(cache, stale)
        return stale, stale_ts if stale is not None else None

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


# --- Display formatting ---


def format_countdown(resets_at_iso: str) -> str:
    try:
        resets_at = datetime.fromisoformat(resets_at_iso)
    except ValueError:
        return ''
    now = datetime.now(UTC)
    secs_left = max(0, int((resets_at - now).total_seconds()))
    d = secs_left // 86400
    h = (secs_left % 86400) // 3600
    m = (secs_left % 3600) // 60
    parts: list[str] = []
    if d:
        parts.append(f'{d}d')
    if d or h:
        parts.append(f'{h}h')
    if not d:
        parts.append(f'{m}m')
    return f'{DIM}{"".join(parts)}{RESET}'


def is_past(resets_at_iso: str) -> bool:
    try:
        return datetime.now(UTC) >= datetime.fromisoformat(resets_at_iso)
    except ValueError:
        return False


def usage_section(label: str, usage: UsageBucket, bar_width: int, *, stale_ts: float | None = None) -> str | None:
    perc = usage.get('utilization')
    resets_at = usage.get('resets_at')
    if perc is None:
        return None
    if resets_at is not None and is_past(resets_at):
        return f'{LABEL}{label}{RESET} {DIM}?{RESET}'
    is_stale = stale_ts is not None
    perc_int = round(perc)
    approx = f'{DIM}\u2265{RESET}' if is_stale else ''
    countdown = format_countdown(resets_at) if resets_at is not None else ''
    return f'{LABEL}{label}{RESET} {bar(perc_int, bar_width)} {approx}{PERC}{perc_int}%{RESET} {countdown}'


def extra_section(extra: ExtraUsage, currency: str, *, stale_ts: float | None = None) -> str | None:
    if not extra.get('is_enabled'):
        return None
    used_cents = extra.get('used_credits')
    if used_cents is None:
        return None
    is_stale = stale_ts is not None
    if stale_ts is not None:
        cached = datetime.fromtimestamp(stale_ts, UTC)
        now = datetime.now(UTC)
        if (cached.year, cached.month) != (now.year, now.month):
            return f'{LABEL}extra{RESET} {DIM}?{RESET}'
    used = used_cents / 100
    limit_cents = extra.get('monthly_limit')
    now = datetime.now(UTC)
    next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    countdown = format_countdown(next_month.isoformat())
    lbl = f'{LABEL}extra{RESET}'
    approx = f'{DIM}\u2265{RESET}' if is_stale else ''
    if limit_cents is not None:
        limit = limit_cents // 100
        return f'{lbl} {approx}{PERC}{used:.2f}{RESET}{DIM}/{RESET}{PERC}{limit}{currency}{RESET} {countdown}'
    return f'{lbl} {approx}{PERC}{used:.2f}{currency}{RESET} {countdown}'


def usage_parts(args: Args, usage: UsageData | None = None, stale_ts: float | None = None) -> list[str]:
    if usage is None:
        return []

    parts: list[str] = []

    buckets = [
        (args.session, 'five_hour', 'sess'),
        (args.weekly, 'seven_day', 'week'),
        (args.opus, 'seven_day_opus', 'opus'),
        (args.sonnet, 'seven_day_sonnet', 'sonnet'),
    ]
    for enabled, key, label in buckets:
        if enabled:
            bucket = usage.get(key)
            if bucket is not None:
                section = usage_section(label, bucket, args.bar_width, stale_ts=stale_ts)
                if section is not None:
                    parts.append(section)

    if args.extra:
        extra = usage.get('extra_usage')
        if extra is not None:
            section = extra_section(extra, args.currency, stale_ts=stale_ts)
            if section is not None:
                parts.append(section)

    return parts


# --- Prompt cache ---


TAIL_CHUNK = 16384


def read_user_timestamps(transcript_path: str) -> tuple[list[float], int | None]:
    timestamps: list[float] = []
    last_user_idx: int | None = None
    try:
        with Path(transcript_path).open('rb') as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return [], None
            read_total = 0
            while read_total < size:
                read_total = min(size, read_total + TAIL_CHUNK)
                f.seek(-read_total, 2)
                tail = f.read(read_total).decode('utf-8', errors='replace')
                timestamps, last_user_idx = parse_user_timestamps(tail)
                if last_user_idx is not None:
                    return timestamps, last_user_idx
    except OSError:
        return [], None
    return timestamps, last_user_idx


def is_user_message(entry: dict[str, Any]) -> bool:
    content = entry.get('message', {}).get('content')
    return isinstance(content, str) and len(content) > 0


def parse_user_timestamps(text: str) -> tuple[list[float], int | None]:
    timestamps: list[float] = []
    last_user_idx: int | None = None
    for line in reversed(text.splitlines()):
        try:
            entry = json.loads(line)
            if entry.get('type') == 'user':
                ts = entry.get('timestamp')
                if ts is not None:
                    timestamps.append(datetime.fromisoformat(ts).timestamp())
                    if last_user_idx is None and is_user_message(entry):
                        last_user_idx = len(timestamps) - 1
        except json.JSONDecodeError, ValueError:
            continue
    return timestamps, last_user_idx


def has_cache_gap(timestamps: list[float], last_user_idx: int | None = None) -> bool:
    """
    Check for cache gaps since the last user message.
    """
    end = len(timestamps) - 1 if last_user_idx is None else min(last_user_idx, len(timestamps) - 1)
    return any(timestamps[i] - timestamps[i + 1] > PROMPT_CACHE_TTL for i in range(end))


def format_cache_countdown(secs_left: int) -> str:
    """
    Format seconds as a compact countdown: "4m" when ≥ 60s, "47s" when < 60s.
    """
    if secs_left >= 60:
        return f'{secs_left // 60}m'
    return f'{secs_left}s'


def prompt_cache_section(transcript_path: str | None, session_id: str | None = None) -> tuple[str | None, float | None]:
    """
    Return (section_string, last_user_timestamp).
    """
    if transcript_path is None:
        return None, None
    timestamps, last_user_idx = read_user_timestamps(transcript_path)
    if not timestamps:
        cached = read_session_cache(session_id) if session_id is not None else {}
        last_ts = cached.get('last_user_ts')
        if last_ts is None:
            return None, None
    else:
        last_ts = timestamps[0]
        if session_id is not None:
            write_session_cache(session_id, {'last_user_ts': last_ts})
    secs_left = max(0, int(last_ts + PROMPT_CACHE_TTL - time.time()))
    gap = has_cache_gap(timestamps, last_user_idx)
    gap_icon = f'{RED}!{RESET} ' if gap else ''

    if secs_left == 0:
        return f'{LABEL}cache{RESET} {gap_icon}{RED}\u2717{RESET}', last_ts

    countdown = format_cache_countdown(secs_left)
    if secs_left <= CACHE_LOW_THRESHOLD:
        return f'{LABEL}cache{RESET} {gap_icon}{YELLOW}\u26a0 {countdown}{RESET}', last_ts
    return f'{LABEL}cache{RESET} {gap_icon}{GREEN}\u2713 {countdown}{RESET}', last_ts


# --- Cache refresh (settings.json trigger) ---


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
        f'from claude_vibeline.statusline import run_refresh_loop; run_refresh_loop({expiry_ts}, {token!r})',
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


# --- Context window ---


def format_context_size(tokens: int) -> str:
    """
    Format token count as human-readable size (e.g. 200_000 → "200k", 1_000_000 → "1M").
    """
    if tokens >= 1_000_000:
        return f'{tokens / 1_000_000:g}M'
    return f'{tokens / 1_000:g}k'


# --- Layout ---


def visible_len(s: str) -> int:
    return len(ANSI_RE.sub('', s))


def wrap_parts(parts: list[str], columns: int) -> str:
    sep_len = visible_len(SEP)
    lines: list[str] = []
    line_parts: list[str] = []
    line_len = 0
    for part in parts:
        part_len = visible_len(part)
        width = (sep_len + part_len) if line_parts else part_len
        if line_parts and line_len + width > columns:
            lines.append(SEP.join(line_parts) + SEP)
            line_parts = [part]
            line_len = part_len
        else:
            line_parts.append(part)
            line_len += width
    if line_parts:
        lines.append(SEP.join(line_parts))
    return '\n'.join(lines)


SUPPORTED_EFFORTS: dict[str, set[str]] = {'opus': {'low', 'medium', 'high', 'max'}, 'sonnet': {'low', 'medium', 'high'}}


def model_family(model_name: str) -> str:
    return model_name.split(maxsplit=1)[0].lower() if model_name else ''


def model_section(model_name: str, effort: str) -> str:
    family = model_family(model_name)
    supported = SUPPORTED_EFFORTS.get(family)
    if supported is None:
        return f'{ORANGE}{model_name}{RESET}'
    if effort.rstrip('?') in supported:
        return f'{ORANGE}{model_name}{RESET} {GOLD}({effort}){RESET}'
    return f'{ORANGE}{model_name}{RESET} {GOLD}(medium?){RESET}'


# --- Debug logging ---


def write_debug_log(  # noqa: PLR0913, PLR0917
    output: str,
    args: Args,
    stdin_data: StdinData | None = None,
    usage_data: UsageData | None = None,
    stale_ts: float | None = None,
    effort: str | None = None,
) -> None:
    try:
        log = debug_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        transcript = stdin_data.get('transcript_path', '') if stdin_data is not None else ''
        session_id = Path(transcript).stem if transcript else None
        entry: dict[str, Any] = {
            'v': app_version,
            'ts': datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S'),
            'session': session_id,
            'output': ANSI_RE.sub('', output).replace(NBSP, ' '),
            'args': dataclasses.asdict(args),
            'stdin': dict(stdin_data) if stdin_data is not None else None,
            'effort': effort,
            'usage': dict(usage_data) if usage_data is not None else None,
            'stale_ts': stale_ts,
        }
        line_bytes = (json.dumps(entry) + '\n').encode('utf-8')
        if log.exists() and log.stat().st_size > DEBUG_LOG_MAX_BYTES:
            content = log.read_bytes()
            # Truncate at a newline boundary to preserve JSONL structure
            mid = len(content) // 2
            newline = content.find(b'\n', mid)
            content = content[newline + 1 :] if newline != -1 else content[mid:]
            fd, tmp = tempfile.mkstemp(dir=log.parent)
            try:
                os.write(fd, content + line_bytes)
                os.close(fd)
                Path(tmp).replace(log)
            except BaseException:
                os.close(fd)
                with contextlib.suppress(OSError):
                    Path(tmp).unlink()
                raise
        else:
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, line_bytes)
            finally:
                os.close(fd)
    except OSError:
        pass


# --- Main ---


def main() -> None:
    args = cappa.parse(Args, completion=False)

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')

    try:
        data: StdinData = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    project_name = Path(data.get('workspace', {}).get('project_dir', '')).name
    model_name = data.get('model', {}).get('display_name') or 'Unknown'
    used_perc = round(data.get('context_window', {}).get('used_percentage', 0))
    effort = resolve_effort(data.get('transcript_path'), data.get('session_id'))

    parts: list[str] = []

    if args.project and project_name and project_name != '.':
        parts.append(f'{CREAM}{project_name}{RESET}')

    if args.model:
        parts.append(model_section(model_name, effort))

    last_user_ts: float | None = None
    if args.cache:
        section, last_user_ts = prompt_cache_section(data.get('transcript_path'), data.get('session_id'))
        if section is not None:
            parts.append(section)

    if args.context:
        ctx_window = data.get('context_window', {}).get('context_window_size')
        ctx_size = f' {DIM}{format_context_size(ctx_window)}{RESET}' if isinstance(ctx_window, int) else ''
        parts.append(f'{LABEL}ctx{RESET}{ctx_size} {bar(used_perc, args.bar_width)} {PERC}{used_perc}%{RESET}')

    usage: UsageData | None = None
    stale_ts: float | None = None
    if args.usage:
        usage, stale_ts = fetch_usage()
        parts.extend(usage_parts(args, usage, stale_ts))

    output = wrap_parts(parts, args.columns)
    print(output)

    if args.cache and args.refresh:
        maybe_spawn_cache_updater(last_user_ts)

    if args.debug:
        write_debug_log(output, args, stdin_data=data, usage_data=usage, stale_ts=stale_ts, effort=effort)
