import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_vibeline.constants import PROMPT_CACHE_TTL, TAIL_CHUNK
from claude_vibeline.display import cache_section
from claude_vibeline.effort import read_session_cache, write_session_cache


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


def prompt_cache_section(
    transcript_path: str | None, session_id: str | None = None, *, live: bool = False
) -> tuple[str | None, float | None]:
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
    secs_left = int(last_ts + PROMPT_CACHE_TTL - time.time())
    gap = has_cache_gap(timestamps, last_user_idx)
    return cache_section(secs_left, gap=gap, live=live), last_ts
