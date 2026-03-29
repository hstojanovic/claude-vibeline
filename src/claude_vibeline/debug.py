import contextlib
import dataclasses
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import platformdirs

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import ANSI_RE, DEBUG_LOG_MAX_BYTES, NBSP

if TYPE_CHECKING:
    from claude_vibeline.args import Args
    from claude_vibeline.schema import StdinData, UsageData


def cleanup_stale_tmp(directory: Path) -> None:
    with contextlib.suppress(OSError):
        for f in directory.iterdir():
            if f.name.startswith('tmp') and f.name != 'debug.log':
                with contextlib.suppress(OSError):
                    f.unlink()


def debug_log_path() -> Path:
    return Path(platformdirs.user_log_dir('claude-vibeline')) / 'debug.log'


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
                with contextlib.suppress(OSError):
                    os.close(fd)
                with contextlib.suppress(OSError):
                    Path(tmp).unlink()
                raise
            cleanup_stale_tmp(log.parent)
        else:
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, line_bytes)
            finally:
                os.close(fd)
    except OSError:
        pass
