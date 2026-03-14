import dataclasses
import io
import json
import os
import re
import subprocess as sp
import sys
import time
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
DEBUG_LOG_MAX_BYTES = 1_000_000


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


class StdinData(TypedDict, total=False):
    workspace: Workspace
    model: Model
    context_window: ContextWindow
    transcript_path: str


@dataclasses.dataclass
@cappa.command(name='claude-vibeline', description=description)
class Args:
    columns: Annotated[int, cappa.Arg(long='--columns', help='terminal width in characters')] = 80
    bar_width: Annotated[int, cappa.Arg(long='--bar-width', help='progress bar width in characters')] = 8
    currency: Annotated[str, cappa.Arg(long='--currency', help='currency symbol for extra usage')] = '€'
    project: Annotated[bool, cappa.Arg(long='--no-project', help='hide project name', show_default=False)] = True
    model: Annotated[bool, cappa.Arg(long='--no-model', help='hide model and effort level', show_default=False)] = True
    cache: Annotated[bool, cappa.Arg(long='--no-cache', help='hide prompt cache status', show_default=False)] = True
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


def read_effort() -> str:
    try:
        settings = Path('~/.claude/settings.json').expanduser()
        return json.loads(settings.read_text()).get('effortLevel', 'default')
    except OSError, json.JSONDecodeError:
        return 'default'


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
            stale_ts = cached.pop('_ts', 0)
            stale = cached or None
            if time.time() - stale_ts < CACHE_TTL_SECONDS:
                return stale, None
    except OSError, json.JSONDecodeError:
        pass

    token = read_oauth_token()
    if token is None:
        write_cache(cache, stale)
        return stale, stale_ts if stale is not None else None

    try:
        resp = requests.get(
            USAGE_URL, headers={'Authorization': f'Bearer {token}', 'anthropic-beta': 'oauth-2025-04-20'}, timeout=3
        )
        resp.raise_for_status()
        data: UsageData = resp.json()
    except requests.RequestException, json.JSONDecodeError:
        write_cache(cache, stale)
        return stale, stale_ts if stale is not None else None

    write_cache(cache, data)
    return data, None


def write_cache(cache: Path, data: UsageData | None) -> None:
    payload: dict[str, Any] = {**(data or {}), '_ts': time.time()}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(payload))
        tmp.replace(cache)
    except OSError:
        pass


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
    is_stale = stale_ts is not None
    if is_stale and resets_at is not None and is_past(resets_at):
        return f'{LABEL}{label}{RESET} {DIM}?{RESET}'
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


def read_last_user_timestamps(transcript_path: str, count: int = 2) -> list[float]:
    timestamps, _ = read_user_timestamps(transcript_path)
    return timestamps[:count]


def read_last_user_timestamp(transcript_path: str) -> float | None:
    timestamps, _ = read_user_timestamps(transcript_path)
    return timestamps[0] if timestamps else None


def has_cache_gap(timestamps: list[float], last_user_idx: int | None = None) -> bool:
    """
    Check for cache gaps since the last user message.
    """
    end = len(timestamps) - 1 if last_user_idx is None else min(last_user_idx, len(timestamps) - 1)
    return any(timestamps[i] - timestamps[i + 1] > PROMPT_CACHE_TTL for i in range(end))


def prompt_cache_section(transcript_path: str | None) -> str | None:
    if transcript_path is None:
        return None
    timestamps, last_user_idx = read_user_timestamps(transcript_path)
    if not timestamps:
        return None
    last_ts = timestamps[0]
    expiry = last_ts + PROMPT_CACHE_TTL
    now = time.time()
    expiry_local = datetime.fromtimestamp(expiry).astimezone()
    hh_mm = expiry_local.strftime('%H:%M')
    if now >= expiry:
        return f'{LABEL}cache{RESET} {RED}\u25cb {hh_mm}{RESET}'
    if has_cache_gap(timestamps, last_user_idx):
        return f'{LABEL}cache{RESET} {YELLOW}\u21bb{RESET} {PERC}{hh_mm}{RESET}'
    return f'{LABEL}cache{RESET} {GREEN}\u25cf{RESET} {PERC}{hh_mm}{RESET}'


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


def model_section(data: StdinData) -> str:
    model_name = data.get('model', {}).get('display_name') or 'Unknown'
    is_haiku = 'haiku' in model_name.lower()
    effort = read_effort() if not is_haiku else None
    if effort == 'default':
        effort = 'high'
    if effort:
        return f'{ORANGE}{model_name}{RESET} {GOLD}({effort}){RESET}'
    return f'{ORANGE}{model_name}{RESET}'


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
        if log.exists() and log.stat().st_size > DEBUG_LOG_MAX_BYTES:
            content = log.read_bytes()
            log.write_bytes(content[len(content) // 2 :])
        transcript = stdin_data.get('transcript_path', '') if stdin_data is not None else ''
        session_id = Path(transcript).stem if transcript else None
        entry: dict[str, Any] = {
            'ts': datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S'),
            'session': session_id,
            'output': ANSI_RE.sub('', output).replace(NBSP, ' '),
            'args': dataclasses.asdict(args),
            'stdin': dict(stdin_data) if stdin_data is not None else None,
            'usage': dict(usage_data) if usage_data is not None else None,
            'stale_ts': stale_ts,
            'effort_from_settings': effort,
        }
        with log.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass


def main() -> None:
    args = cappa.parse(Args, completion=False)

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')

    try:
        data: StdinData = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    project_dir = data.get('workspace', {}).get('project_dir', '')
    project_name = Path(project_dir).name
    used_perc = round(data.get('context_window', {}).get('used_percentage', 0))

    parts: list[str] = []

    if args.project and project_name and project_name != '.':
        parts.append(f'{CREAM}{project_name}{RESET}')

    if args.model:
        parts.append(model_section(data))

    if args.cache:
        section = prompt_cache_section(data.get('transcript_path'))
        if section is not None:
            parts.append(section)

    if args.context:
        parts.append(f'{LABEL}ctx{RESET} {bar(used_perc, args.bar_width)} {PERC}{used_perc}%{RESET}')

    usage: UsageData | None = None
    stale_ts: float | None = None
    if args.usage:
        usage, stale_ts = fetch_usage()
        parts.extend(usage_parts(args, usage, stale_ts))

    output = wrap_parts(parts, args.columns)
    print(output)

    if args.debug:
        effort = read_effort()
        write_debug_log(output, args, stdin_data=data, usage_data=usage, stale_ts=stale_ts, effort=effort)
