import contextlib
import io
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from claude_vibeline.args import parse_args
from claude_vibeline.constants import CREAM, DIM, LABEL, PERC, RESET
from claude_vibeline.debug import write_debug_log
from claude_vibeline.display import (
    api_usage_parts,
    bar,
    format_context_size,
    format_error_message,
    model_section,
    stdin_usage_parts,
    wrap_message,
    wrap_parts,
)
from claude_vibeline.effort import refine_effort_for_model, resolve_effort, session_cache_dir
from claude_vibeline.prompt_cache import prompt_cache_section
from claude_vibeline.update import check_for_update, format_update_message
from claude_vibeline.usage import fetch_usage

if TYPE_CHECKING:
    from claude_vibeline.args import Args
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


def is_new_session(session_id: str | None) -> bool:
    if session_id is None:
        return False
    try:
        return not (session_cache_dir() / f'{session_id}.json').exists()
    except OSError:
        return False


def render(args: Args, data: StdinData) -> tuple[str, str, UsageData | None, float | None]:
    """
    Render the statusline from args + stdin data.

    Returns (output, effort, api_usage, stale_ts). May raise on unexpected
    failure — the caller converts it into an error message.
    """
    project_name = Path(data.get('workspace', {}).get('project_dir', '')).name
    model_name = data.get('model', {}).get('display_name') or 'Unknown'
    used_perc = round(data.get('context_window', {}).get('used_percentage') or 0)
    effort = resolve_effort(data.get('transcript_path'), data.get('session_id'))
    effort = refine_effort_for_model(effort, model_name)

    parts: list[str] = []

    if args.project and project_name and project_name != '.':
        parts.append(f'{CREAM}{project_name}{RESET}')

    if args.model:
        parts.append(model_section(model_name, effort))

    if args.cache:
        section = prompt_cache_section(data.get('transcript_path'), data.get('session_id'))
        if section is not None:
            parts.append(section)

    if args.context:
        ctx_window = data.get('context_window', {}).get('context_window_size')
        ctx_size = f' {DIM}{format_context_size(ctx_window)}{RESET}' if isinstance(ctx_window, int) else ''
        parts.append(f'{LABEL}ctx{RESET}{ctx_size} {bar(used_perc, args.bar_width)} {PERC}{used_perc}%{RESET}')

    usage_result, api_usage, stale_ts = collect_usage(args, data)
    parts.extend(usage_result)

    return wrap_parts(parts, args.columns), effort, api_usage, stale_ts


def get_update_message(args: Args, *, new_session: bool) -> str | None:
    if not args.update:
        return None
    with contextlib.suppress(Exception):
        latest = check_for_update(is_new_session=new_session)
        if latest is not None:
            return format_update_message(latest)
    return None


def main() -> None:
    args, parse_error = parse_args()

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')

    try:
        data: StdinData = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    output = ''
    effort = ''
    api_usage: UsageData | None = None
    stale_ts: float | None = None
    error_msg: str | None = parse_error
    new_session = is_new_session(data.get('session_id'))

    try:
        output, effort, api_usage, stale_ts = render(args, data)
    except Exception as e:  # noqa: BLE001 — any render failure surfaces as an error message
        if error_msg is None:
            error_msg = f'{type(e).__name__}: {e}'

    message = (
        format_error_message(error_msg) if error_msg is not None else get_update_message(args, new_session=new_session)
    )
    if message is not None:
        wrapped = wrap_message(message, args.columns)
        output = f'{output}\n{wrapped}' if output else wrapped

    if output:
        print(output)

    if args.debug:
        write_debug_log(output, args, stdin_data=data, usage_data=api_usage, stale_ts=stale_ts, effort=effort)
