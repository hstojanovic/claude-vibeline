import io
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import cappa

from claude_vibeline.args import Args
from claude_vibeline.constants import CREAM, DIM, LABEL, PERC, RESET
from claude_vibeline.debug import write_debug_log
from claude_vibeline.display import (
    api_usage_parts,
    bar,
    format_context_size,
    model_section,
    stdin_usage_parts,
    wrap_parts,
)
from claude_vibeline.effort import resolve_effort
from claude_vibeline.prompt_cache import prompt_cache_section
from claude_vibeline.refresh import maybe_spawn_cache_updater
from claude_vibeline.usage import fetch_usage

if TYPE_CHECKING:
    from claude_vibeline.schema import StdinData, UsageData


def collect_usage(args: Args, data: StdinData) -> tuple[list[str], UsageData | None, float | None]:
    parts: list[str] = []
    stdin_limits = data.get('rate_limits')
    if stdin_limits is not None:
        parts.extend(stdin_usage_parts(args, stdin_limits))
    api_usage: UsageData | None = None
    stale_ts: float | None = None
    if args.usage and (args.opus or args.sonnet or args.extra):
        api_usage, stale_ts = fetch_usage()
        if api_usage is not None:
            parts.extend(api_usage_parts(args, api_usage, stale_ts))
    return parts, api_usage, stale_ts


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
        section, last_user_ts = prompt_cache_section(
            data.get('transcript_path'), data.get('session_id'), live=args.refresh
        )
        if section is not None:
            parts.append(section)

    if args.context:
        ctx_window = data.get('context_window', {}).get('context_window_size')
        ctx_size = f' {DIM}{format_context_size(ctx_window)}{RESET}' if isinstance(ctx_window, int) else ''
        parts.append(f'{LABEL}ctx{RESET}{ctx_size} {bar(used_perc, args.bar_width)} {PERC}{used_perc}%{RESET}')

    usage_result, api_usage, stale_ts = collect_usage(args, data)
    parts.extend(usage_result)

    output = wrap_parts(parts, args.columns)
    print(output)

    if args.cache and args.refresh:
        maybe_spawn_cache_updater(last_user_ts)

    if args.debug:
        write_debug_log(output, args, stdin_data=data, usage_data=api_usage, stale_ts=stale_ts, effort=effort)
