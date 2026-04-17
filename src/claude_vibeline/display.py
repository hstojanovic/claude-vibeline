import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from claude_vibeline.constants import (
    ANSI_RE,
    BAR_EMPTY,
    CACHE_LOW_THRESHOLD,
    DIM,
    EMPTY,
    FILL,
    GOLD,
    GREEN,
    LABEL,
    ORANGE,
    PERC,
    RED,
    RESET,
    SEP,
    YELLOW,
)
from claude_vibeline.effort import supported_efforts_for

if TYPE_CHECKING:
    from claude_vibeline.args import Args
    from claude_vibeline.schema import ExtraUsage, StdinRateLimitBucket, StdinRateLimits, UsageBucket, UsageData


def bar(perc: int, width: int) -> str:
    width = max(0, width)
    if not width:
        return ''
    perc = max(0, min(100, perc))
    filled = round(perc * width / 100)
    empty = width - filled
    return f'{ORANGE}{FILL * filled}{BAR_EMPTY}{EMPTY * empty}{RESET}'


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


def format_countdown_ts(resets_at_ts: int) -> str:
    secs_left = max(0, resets_at_ts - int(time.time()))
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


def stdin_section(label: str, limit: StdinRateLimitBucket, bar_width: int) -> str | None:
    perc = limit.get('used_percentage')
    if perc is None:
        return None
    resets_at = limit.get('resets_at')
    if resets_at is not None and time.time() >= resets_at:
        return f'{LABEL}{label}{RESET} {DIM}?{RESET}'
    perc_int = round(perc)
    countdown = format_countdown_ts(resets_at) if resets_at is not None else ''
    return f'{LABEL}{label}{RESET} {bar(perc_int, bar_width)} {PERC}{perc_int}%{RESET} {countdown}'


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


def stdin_usage_parts(args: Args, stdin_limits: StdinRateLimits) -> list[str]:
    parts: list[str] = []
    if args.session and 'five_hour' in stdin_limits:
        section = stdin_section('sess', stdin_limits['five_hour'], args.bar_width)
        if section is not None:
            parts.append(section)
    if args.weekly and 'seven_day' in stdin_limits:
        section = stdin_section('week', stdin_limits['seven_day'], args.bar_width)
        if section is not None:
            parts.append(section)
    return parts


def api_usage_parts(args: Args, api_usage: UsageData, stale_ts: float | None = None) -> list[str]:
    parts: list[str] = []
    for enabled, key, label in [(args.opus, 'seven_day_opus', 'opus'), (args.sonnet, 'seven_day_sonnet', 'sonnet')]:
        if enabled:
            bucket = api_usage.get(key)
            if bucket is not None:
                section = usage_section(label, bucket, args.bar_width, stale_ts=stale_ts)
                if section is not None:
                    parts.append(section)

    if args.extra:
        extra = api_usage.get('extra_usage')
        if extra is not None:
            section = extra_section(extra, args.currency, stale_ts=stale_ts)
            if section is not None:
                parts.append(section)

    return parts


def model_section(model_name: str, effort: str) -> str:
    supported = supported_efforts_for(model_name)
    if supported is None:
        return f'{ORANGE}{model_name}{RESET}'
    bare = effort.rstrip('?')
    if bare in supported:
        return f'{ORANGE}{model_name}{RESET} {GOLD}({effort}){RESET}'
    if bare == 'xhigh' and 'high' in supported:
        return f'{ORANGE}{model_name}{RESET} {GOLD}(high?){RESET}'
    return f'{ORANGE}{model_name}{RESET} {GOLD}(medium?){RESET}'


def format_context_size(tokens: int) -> str:
    """
    Format token count as human-readable size (e.g. 200_000 -> "200k", 1_000_000 -> "1M").
    """
    if tokens >= 1_000_000:
        return f'{tokens / 1_000_000:g}M'
    return f'{tokens / 1_000:g}k'


def format_cache_countdown(secs_left: int) -> str:
    """
    Format seconds as a compact countdown: "4m" when >= 60s, "47s" when < 60s.
    """
    if secs_left >= 60:
        return f'{secs_left // 60}m'
    return f'{secs_left}s'


def format_cache_expiry(secs_left: int) -> str:
    """
    Format cache expiration as local clock time (e.g. "14:35").
    """
    expiry = datetime.now(tz=None) + timedelta(seconds=secs_left)  # noqa: DTZ005 — local clock time intentional
    return expiry.strftime('%H:%M')


def cache_section(secs_left: int, *, gap: bool, live: bool = False) -> str:
    gap_icon = f'{RED}\u21bb{RESET} ' if gap else ''
    if secs_left <= 0:
        display = '0s' if live else format_cache_expiry(secs_left)
        return f'{LABEL}cache{RESET} {gap_icon}{RED}\u2717 {display}{RESET}'
    display = format_cache_countdown(secs_left) if live else format_cache_expiry(secs_left)
    if secs_left <= CACHE_LOW_THRESHOLD:
        return f'{LABEL}cache{RESET} {gap_icon}{YELLOW}\u26a0 {display}{RESET}'
    return f'{LABEL}cache{RESET} {gap_icon}{GREEN}\u25f7 {display}{RESET}'


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
