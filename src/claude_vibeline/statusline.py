import io
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import cappa

from claude_vibeline.args import Args
from claude_vibeline.constants import CREAM, DIM, LABEL, PERC, RESET
from claude_vibeline.debug import write_debug_log
from claude_vibeline.display import bar, format_context_size, model_section, usage_parts, wrap_parts
from claude_vibeline.effort import resolve_effort
from claude_vibeline.prompt_cache import prompt_cache_section
from claude_vibeline.refresh import maybe_spawn_cache_updater
from claude_vibeline.usage import fetch_usage

if TYPE_CHECKING:
    from claude_vibeline.schema import StdinData, UsageData


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
