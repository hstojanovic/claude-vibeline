"""
Unit tests for `prompt_cache.py`.

Covers tail-chunked transcript reading, user-message detection (incl.
tool_result), cache-gap detection relative to last user message, and
rendered-section state per cache age.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import ANSI_RE, CACHE_LOW_THRESHOLD, PROMPT_CACHE_TTL, TAIL_CHUNK
from claude_vibeline.effort import read_session_cache
from claude_vibeline.prompt_cache import has_cache_gap, prompt_cache_section, read_user_timestamps


def _user(ts: str) -> str:
    return json.dumps({'type': 'user', 'timestamp': ts, 'message': {'content': 'hello'}})


def _tool_result(ts: str) -> str:
    return json.dumps({
        'type': 'user',
        'timestamp': ts,
        'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'x', 'content': 'ok'}]},
    })


def _assistant(ts: str) -> str:
    return json.dumps({'type': 'assistant', 'timestamp': ts})


class TestReadUserTimestamps:
    def test_returns_all(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = [
            _user('2026-03-07T10:00:00Z'),
            _assistant('2026-03-07T10:00:30Z'),
            _tool_result('2026-03-07T10:01:00Z'),
            _assistant('2026-03-07T10:01:30Z'),
            _tool_result('2026-03-07T10:02:00Z'),
        ]
        transcript.write_text('\n'.join(lines) + '\n')

        timestamps, _ = read_user_timestamps(str(transcript))
        assert len(timestamps) == 3

    def test_last_user_idx(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = [
            _user('2026-03-07T10:00:00Z'),
            _assistant('2026-03-07T10:00:30Z'),
            _tool_result('2026-03-07T10:01:00Z'),
            _assistant('2026-03-07T10:01:30Z'),
            _tool_result('2026-03-07T10:02:00Z'),
        ]
        transcript.write_text('\n'.join(lines) + '\n')

        _, last_user_idx = read_user_timestamps(str(transcript))
        assert last_user_idx == 2

    def test_user_is_most_recent(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = [_user('2026-03-07T10:00:00Z'), _assistant('2026-03-07T10:00:30Z'), _user('2026-03-07T10:01:00Z')]
        transcript.write_text('\n'.join(lines) + '\n')

        _, last_user_idx = read_user_timestamps(str(transcript))
        assert last_user_idx == 0

    def test_single_user_message(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_user('2026-03-07T10:00:00Z') + '\n')

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == 1
        assert last_user_idx == 0

    def test_no_user_messages(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_assistant('2026-03-07T10:00:00Z') + '\n')

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert timestamps == []
        assert last_user_idx is None

    def test_empty_file(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text('')
        timestamps, _ = read_user_timestamps(str(transcript))
        assert timestamps == []

    def test_missing_file(self) -> None:
        timestamps, _ = read_user_timestamps('/nonexistent/path.jsonl')
        assert timestamps == []

    def test_tool_result_counted_as_user(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = [
            _user('2026-03-07T10:00:00Z'),
            _assistant('2026-03-07T10:00:05Z'),
            _tool_result('2026-03-07T10:06:00Z'),
        ]
        transcript.write_text('\n'.join(lines) + '\n')

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == 2
        gap = timestamps[0] - timestamps[1]
        assert abs(gap - 360.0) < 1
        assert last_user_idx == 1

    def test_no_message_field_treated_as_non_user(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = [
            json.dumps({'type': 'user', 'timestamp': '2026-03-07T10:00:00Z'}),
            json.dumps({'type': 'user', 'timestamp': '2026-03-07T10:01:00Z'}),
        ]
        transcript.write_text('\n'.join(lines) + '\n')

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == 2
        assert last_user_idx is None

    def test_binary_garbage_in_transcript(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_bytes(b'\x80\xff\xfe\x00' * 100)
        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert timestamps == []
        assert last_user_idx is None

    def test_mixed_valid_and_corrupt_lines(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = ['{bad json', _user('2026-03-07T10:00:00Z'), 'not even close', _assistant('2026-03-07T10:01:00Z')]
        transcript.write_text('\n'.join(lines) + '\n')
        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == 1
        assert last_user_idx == 0

    def test_user_entry_with_invalid_timestamp(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        entry = json.dumps({'type': 'user', 'timestamp': 'not-a-timestamp', 'message': {'content': 'hi'}})
        transcript.write_text(entry + '\n')
        timestamps, _ = read_user_timestamps(str(transcript))
        assert timestamps == []

    def test_user_entry_with_numeric_timestamp(self, tmp_path: Path) -> None:
        # A non-string timestamp must be skipped gracefully, not raise TypeError out of the parse.
        transcript = tmp_path / 'session.jsonl'
        entry = json.dumps({'type': 'user', 'timestamp': 1759000000, 'message': {'content': 'hi'}})
        transcript.write_text(entry + '\n')
        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert timestamps == []
        assert last_user_idx is None


class TestHasCacheGap:
    def test_no_gap(self) -> None:
        now = time.time()
        assert not has_cache_gap([now, now - 60, now - 120], last_user_idx=2)

    def test_gap_after_user(self) -> None:
        now = time.time()
        assert has_cache_gap([now, now - PROMPT_CACHE_TTL - 1], last_user_idx=1)

    def test_gap_before_user_ignored(self) -> None:
        now = time.time()
        timestamps = [now, now - 30, now - PROMPT_CACHE_TTL - 60]
        assert not has_cache_gap(timestamps, last_user_idx=0)

    def test_gap_after_user_in_middle(self) -> None:
        now = time.time()
        timestamps = [now, now - PROMPT_CACHE_TTL - 10, now - PROMPT_CACHE_TTL - 20, now - PROMPT_CACHE_TTL - 30]
        assert has_cache_gap(timestamps, last_user_idx=2)

    def test_none_idx_checks_all(self) -> None:
        now = time.time()
        assert has_cache_gap([now, now - PROMPT_CACHE_TTL - 1], last_user_idx=None)

    def test_single_entry(self) -> None:
        assert not has_cache_gap([time.time()], last_user_idx=0)

    def test_empty(self) -> None:
        assert not has_cache_gap([])


class TestPromptCacheSection:
    def test_no_transcript_renders_pending(self) -> None:
        result = prompt_cache_section(None)
        assert 'cache' in result
        assert '—' in result

    def test_warm_cache(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert 'cache' in result
        assert '\u25f7' in result
        assert 'm' in result

    def test_warm_cache_low(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = (datetime.now(UTC) - timedelta(seconds=PROMPT_CACHE_TTL - CACHE_LOW_THRESHOLD + 10)).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u26a0' in result
        assert '\u25f7' not in result

    def test_expired_cache(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_ts = (datetime.now(UTC) - timedelta(seconds=PROMPT_CACHE_TTL + 10)).isoformat()
        transcript.write_text(_user(old_ts) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u2717' in result
        assert 'm' not in ANSI_RE.sub('', result).split('\u2717')[-1]

    def test_recached_after_gap(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        now = datetime.now(UTC)
        user_ts = (now - timedelta(seconds=PROMPT_CACHE_TTL + 60)).isoformat()
        recent_ts = now.isoformat()
        lines = [_user(user_ts), _assistant(user_ts), _tool_result(recent_ts)]
        transcript.write_text('\n'.join(lines) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u21bb' in result
        assert '\u25f7' in result
        assert '\u2717' not in result

    def test_no_recache_indicator_when_no_gap(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        now = datetime.now(UTC)
        user_ts = (now - timedelta(seconds=60)).isoformat()
        recent_ts = now.isoformat()
        lines = [_user(user_ts), _assistant(user_ts), _tool_result(recent_ts)]
        transcript.write_text('\n'.join(lines) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u25f7' in result
        assert '\u21bb' not in result
        assert '\u2717' not in result

    def test_no_recache_indicator_single_message(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u21bb' not in result

    def test_gap_before_user_ignored(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        now = datetime.now(UTC)
        lines = [
            _tool_result((now - timedelta(seconds=PROMPT_CACHE_TTL + 60)).isoformat()),
            _assistant((now - timedelta(seconds=30)).isoformat()),
            _user((now - timedelta(seconds=30)).isoformat()),
            _assistant((now - timedelta(seconds=15)).isoformat()),
            _tool_result(now.isoformat()),
        ]
        transcript.write_text('\n'.join(lines) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u25f7' in result
        assert '\u21bb' not in result

    def test_gap_after_user_shows_recached(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        now = datetime.now(UTC)
        lines = [
            _user((now - timedelta(seconds=PROMPT_CACHE_TTL + 60)).isoformat()),
            _assistant((now - timedelta(seconds=PROMPT_CACHE_TTL + 50)).isoformat()),
            _tool_result((now - timedelta(seconds=30)).isoformat()),
            _assistant((now - timedelta(seconds=15)).isoformat()),
            _tool_result(now.isoformat()),
        ]
        transcript.write_text('\n'.join(lines) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u21bb' in result

    def test_no_user_messages_renders_pending(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_assistant('2026-03-07T10:00:00Z') + '\n')
        result = prompt_cache_section(str(transcript))
        assert 'cache' in result
        assert '—' in result

    def test_caches_last_user_ts(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir):
            prompt_cache_section(str(transcript), 'sess-1')
            cached = read_session_cache('sess-1')
            assert 'last_user_ts' in cached

    def test_falls_back_to_cached_last_user_ts(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_assistant('2026-03-07T10:00:00Z') + '\n')
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        cached_ts = time.time() - 60
        (cache_dir / 'sess-1.json').write_text(json.dumps({'last_user_ts': cached_ts, '_v': app_version}))
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir):
            result = prompt_cache_section(str(transcript), 'sess-1')
        assert result is not None
        assert '\u25f7' in result


class TestChunkedTranscriptReading:
    def test_user_found_in_second_chunk(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        user_line = _user('2026-03-07T10:00:00Z')
        filler_line = _assistant('2026-03-07T10:00:30Z')
        filler_count = (TAIL_CHUNK // len(filler_line)) + 10
        lines = [user_line] + [filler_line] * filler_count
        transcript.write_text('\n'.join(lines) + '\n')
        assert transcript.stat().st_size > TAIL_CHUNK

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == 1
        assert last_user_idx == 0

    def test_no_user_reads_entire_file(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        filler_line = _tool_result('2026-03-07T10:00:30Z')
        filler_count = (TAIL_CHUNK // len(filler_line)) + 10
        lines = [filler_line] * filler_count
        transcript.write_text('\n'.join(lines) + '\n')
        assert transcript.stat().st_size > TAIL_CHUNK

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == filler_count
        assert last_user_idx is None


class TestIsUserMessage:
    def test_empty_string_content_not_user(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        entry = json.dumps({'type': 'user', 'timestamp': '2026-03-07T10:00:00Z', 'message': {'content': ''}})
        transcript.write_text(entry + '\n')

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == 1
        assert last_user_idx is None

    def test_list_content_not_user(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        entry = json.dumps({
            'type': 'user',
            'timestamp': '2026-03-07T10:00:00Z',
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'x', 'content': 'ok'}]},
        })
        transcript.write_text(entry + '\n')

        timestamps, last_user_idx = read_user_timestamps(str(transcript))
        assert len(timestamps) == 1
        assert last_user_idx is None
