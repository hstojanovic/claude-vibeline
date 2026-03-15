import io
import json
import os
import runpy
import subprocess as sp
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from unittest import mock

import responses
from freezegun import freeze_time

from claude_vibeline import __version__ as app_version
from claude_vibeline.statusline import (
    ANSI_RE,
    CACHE_LOW_THRESHOLD,
    CACHE_TTL_SECONDS,
    DEBUG_LOG_MAX_BYTES,
    EMPTY,
    FILL,
    NBSP,
    ORANGE,
    PERC,
    PROMPT_CACHE_TTL,
    RESET,
    SEP,
    TAIL_CHUNK,
    USAGE_URL,
    Args,
    EffortScanner,
    bar,
    cache_path,
    cleanup_session_cache,
    debug_log_path,
    extra_section,
    fetch_usage,
    format_cache_countdown,
    format_context_size,
    format_countdown,
    has_cache_gap,
    is_lock_owner,
    is_past,
    main,
    model_section,
    parse_effort_from_line,
    prompt_cache_section,
    read_oauth_token,
    read_session_cache,
    read_settings_effort,
    read_user_timestamps,
    resolve_effort,
    run_refresh_loop,
    scan_transcript_effort,
    session_cache_dir,
    spawn_cache_updater,
    toggle_settings_space,
    token_from_entry,
    usage_parts,
    usage_section,
    visible_len,
    wrap_parts,
    write_debug_log,
    write_session_cache,
    write_usage_cache,
)

if TYPE_CHECKING:
    from claude_vibeline.statusline import ExtraUsage, StdinData, UsageBucket, UsageData


class TestBar:
    def test_width_zero(self) -> None:
        result = bar(50, 0)
        assert not result

    def test_width_8_pct_0(self) -> None:
        result = bar(0, 8)
        assert result.count(EMPTY) == 8
        assert FILL not in result

    def test_width_8_pct_50(self) -> None:
        result = bar(50, 8)
        assert result.count(FILL) == 4
        assert result.count(EMPTY) == 4

    def test_width_8_pct_100(self) -> None:
        result = bar(100, 8)
        assert result.count(FILL) == 8
        assert EMPTY not in result

    def test_negative_pct_clamped(self) -> None:
        result = bar(-10, 8)
        assert result.count(EMPTY) == 8

    def test_pct_over_100_clamped(self) -> None:
        result = bar(200, 8)
        assert result.count(FILL) == 8

    def test_negative_width_clamped(self) -> None:
        result = bar(50, -5)
        assert not result


class TestIsPast:
    @freeze_time('2026-03-07T12:00:00Z')
    def test_future(self) -> None:
        assert not is_past('2026-03-07T15:00:00+00:00')

    @freeze_time('2026-03-07T12:00:00Z')
    def test_past(self) -> None:
        assert is_past('2026-03-07T10:00:00+00:00')

    def test_invalid(self) -> None:
        assert not is_past('not-a-date')


class TestUsageSection:
    def test_valid_data(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2099-01-01T00:00:00+00:00'}
        result = usage_section('sess', usage, 8)
        assert result is not None
        assert 'sess' in result
        assert '42%' in result
        assert '\u2265' not in result

    def test_none_pct(self) -> None:
        usage: UsageBucket = {'utilization': None, 'resets_at': '2099-01-01T00:00:00+00:00'}
        result = usage_section('sess', usage, 8)
        assert result is None

    def test_without_resets_at(self) -> None:
        usage: UsageBucket = {'utilization': 25}
        result = usage_section('week', usage, 8)
        assert result is not None
        assert '25%' in result

    def test_stale_within_window(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2099-01-01T00:00:00+00:00'}
        result = usage_section('sess', usage, 8, stale_ts=time.time() - 120)
        assert result is not None
        assert '\u2265' in result
        assert '42%' in result
        assert '?' not in result

    @freeze_time('2026-03-07T12:00:00Z')
    def test_stale_past_reset(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2026-03-07T10:00:00+00:00'}
        result = usage_section('sess', usage, 8, stale_ts=time.time() - 120)
        assert result is not None
        assert '?' in result
        assert '42%' not in result
        assert FILL not in result

    @freeze_time('2026-03-07T12:00:00Z')
    def test_fresh_past_reset(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2026-03-07T10:00:00+00:00'}
        result = usage_section('sess', usage, 8)
        assert result is not None
        assert '?' in result
        assert '42%' not in result
        assert FILL not in result


class TestExtraSection:
    def test_enabled_with_limit(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 123, 'monthly_limit': 2000}
        result = extra_section(extra, '$')
        assert result is not None
        assert 'extra' in result
        assert '1.23' in result
        assert '20$' in result

    def test_enabled_without_limit(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 500}
        result = extra_section(extra, '€')
        assert result is not None
        assert '5.00€' in result

    def test_disabled(self) -> None:
        extra: ExtraUsage = {'is_enabled': False, 'used_credits': 100, 'monthly_limit': 2000}
        result = extra_section(extra, '$')
        assert result is None

    def test_missing_used_credits(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'monthly_limit': 2000}
        result = extra_section(extra, '$')
        assert result is None

    @freeze_time('2026-02-15T12:00:00Z')
    def test_countdown_to_next_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 100, 'monthly_limit': 2000}
        result = extra_section(extra, '$')
        assert result is not None
        assert '13d' in result

    @freeze_time('2026-03-15T12:00:00Z')
    def test_stale_same_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000}
        stale_ts = time.time() - 120
        result = extra_section(extra, '$', stale_ts=stale_ts)
        assert result is not None
        assert '\u2265' in result
        assert '2.50' in result
        assert '?' not in result

    @freeze_time('2026-03-01T00:30:00Z')
    def test_stale_previous_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000}
        stale_ts = time.time() - 3600
        result = extra_section(extra, '$', stale_ts=stale_ts)
        assert result is not None
        assert '?' in result
        assert '2.50' not in result


class TestCachePath:
    def test_returns_expected_path(self) -> None:
        path = cache_path()
        assert isinstance(path, Path)
        assert path.name == 'usage.json'
        assert 'claude-vibeline' in str(path)


class TestDebugLogPath:
    def test_returns_expected_path(self) -> None:
        path = debug_log_path()
        assert isinstance(path, Path)
        assert path.name == 'debug.log'
        assert 'claude-vibeline' in str(path)


class TestFormatCountdown:
    @freeze_time('2026-02-24T10:00:00Z')
    def test_future_days_and_hours(self) -> None:
        result = format_countdown('2026-02-27T14:30:00+00:00')
        assert '3d' in result
        assert '4h' in result

    @freeze_time('2026-02-24T10:00:00Z')
    def test_hours_and_minutes(self) -> None:
        result = format_countdown('2026-02-24T13:45:00+00:00')
        assert '3h' in result
        assert '45m' in result
        assert 'd' not in result

    @freeze_time('2026-02-24T10:00:00Z')
    def test_past_timestamp_clamps_to_zero(self) -> None:
        result = format_countdown('2026-02-20T00:00:00+00:00')
        assert '0m' in result

    def test_invalid_iso_string(self) -> None:
        result = format_countdown('not-a-date')
        assert not result


class TestFormatCacheCountdown:
    def test_minutes(self) -> None:
        assert format_cache_countdown(240) == '4m'

    def test_exactly_60(self) -> None:
        assert format_cache_countdown(60) == '1m'

    def test_seconds(self) -> None:
        assert format_cache_countdown(47) == '47s'

    def test_zero(self) -> None:
        assert format_cache_countdown(0) == '0s'


def transcript_line(content: str) -> str:
    return json.dumps({'type': 'user', 'message': {'content': content}})


class TestParseEffortFromLine:
    def test_model_command_low(self) -> None:
        assert parse_effort_from_line('Set model to Sonnet 4.6 with low effort') == 'low'

    def test_model_command_medium(self) -> None:
        assert parse_effort_from_line('Set model to Sonnet 4.6 with medium effort') == 'medium'

    def test_model_command_high(self) -> None:
        assert parse_effort_from_line('Set model to Opus 4.6 (1M context) (default) with high effort') == 'high'

    def test_model_command_max(self) -> None:
        assert parse_effort_from_line('Set model to Opus 4.6 with max effort') == 'max'

    def test_model_command_no_effort(self) -> None:
        assert parse_effort_from_line('Set model to Sonnet 4.6') is None

    def test_effort_command_low(self) -> None:
        assert parse_effort_from_line('Set effort level to low') == 'low'

    def test_effort_command_medium(self) -> None:
        assert parse_effort_from_line('Set effort level to medium') == 'medium'

    def test_effort_command_high(self) -> None:
        assert parse_effort_from_line('Set effort level to high') == 'high'

    def test_effort_command_max(self) -> None:
        assert parse_effort_from_line('Set effort level to max') == 'max'

    def test_effort_auto(self) -> None:
        assert parse_effort_from_line('Effort level set to auto') == 'auto'

    def test_unrelated_text(self) -> None:
        assert parse_effort_from_line('hello world') is None


class TestEffortScanner:
    def test_effort_from_content(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({
            'message': {'content': '<local-command-stdout>Set effort level to high</local-command-stdout>'}
        })
        assert scanner.effort == 'high'
        assert scanner.done

    def test_auto_effort_becomes_medium(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({
            'message': {'content': '<local-command-stdout>Effort level set to auto</local-command-stdout>'}
        })
        assert scanner.effort == 'medium'
        assert scanner.done

    def test_synthetic_before_effort_invalidates(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'No response requested.'}]}
        })
        assert scanner.saw_synthetic
        scanner.process_entry({
            'message': {'content': '<local-command-stdout>Set effort level to high</local-command-stdout>'}
        })
        assert scanner.effort is None
        assert scanner.done

    def test_api_error_synthetic_does_not_set_flag(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'API Error: 400'}]}
        })
        assert not scanner.saw_synthetic

    def test_since_ts_skips_old_entries(self) -> None:
        scanner = EffortScanner('2026-03-15T10:00:00Z')
        scanner.process_entry({
            'timestamp': '2026-03-15T09:00:00Z',
            'message': {'content': '<local-command-stdout>Set effort level to max</local-command-stdout>'},
        })
        assert scanner.done
        assert scanner.effort is None

    def test_since_ts_processes_new_entries(self) -> None:
        scanner = EffortScanner('2026-03-15T10:00:00Z')
        scanner.process_entry({
            'timestamp': '2026-03-15T11:00:00Z',
            'message': {'content': '<local-command-stdout>Set effort level to max</local-command-stdout>'},
        })
        assert scanner.effort == 'max'

    def test_latest_ts_tracked(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({'timestamp': '2026-03-15T10:00:00Z', 'message': {'content': 'hello'}})
        scanner.process_entry({'timestamp': '2026-03-15T09:00:00Z', 'message': {'content': 'world'}})
        assert scanner.latest_ts == '2026-03-15T10:00:00Z'

    def test_non_string_content_ignored(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({'message': {'content': [{'type': 'tool_result'}]}})
        assert scanner.effort is None
        assert not scanner.done

    def test_initial_state(self) -> None:
        scanner = EffortScanner('')
        assert scanner.effort is None
        assert not scanner.saw_synthetic
        assert not scanner.done
        assert not scanner.latest_ts

    def test_synthetic_non_list_content(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({'message': {'model': '<synthetic>', 'content': 'plain text'}})
        assert not scanner.saw_synthetic

    def test_synthetic_with_non_matching_text(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'Something else'}]}
        })
        assert not scanner.saw_synthetic

    def test_synthetic_with_non_dict_blocks(self) -> None:
        scanner = EffortScanner('')
        scanner.process_entry({'message': {'model': '<synthetic>', 'content': ['not a dict', 42]}})
        assert not scanner.saw_synthetic


class TestScanTranscriptEffort:
    def test_effort_command(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        content = '<local-command-stdout>Set effort level to high</local-command-stdout>'
        transcript.write_text(transcript_line(content) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort == 'high'

    def test_model_command_with_effort(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        content = (
            '<local-command-stdout>Set model to Opus 4.6 (1M context) (default) with max effort</local-command-stdout>'
        )
        transcript.write_text(transcript_line(content) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort == 'max'

    def test_effort_auto_returns_medium(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        content = '<local-command-stdout>Effort level set to auto</local-command-stdout>'
        transcript.write_text(transcript_line(content) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort == 'medium'

    def test_ansi_codes_stripped(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        content = (
            '<local-command-stdout>'
            'Set model to \033[1mOpus 4.6\033[0m with \033[1mhigh\033[0m effort'
            '</local-command-stdout>'
        )
        transcript.write_text(transcript_line(content) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort == 'high'

    def test_no_effort_command(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(transcript_line('hello') + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort is None

    def test_none_path(self) -> None:
        effort, ts, _ = scan_transcript_effort(None)
        assert effort is None
        assert not ts

    def test_empty_file(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text('')
        effort, ts, _ = scan_transcript_effort(str(transcript))
        assert effort is None
        assert not ts

    def test_missing_file(self, tmp_path: Path) -> None:
        effort, _, _ = scan_transcript_effort(str(tmp_path / 'missing.jsonl'))
        assert effort is None

    def test_uses_last_command(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'

        def effort_msg(level: str) -> str:
            content = f'<local-command-stdout>Set effort level to {level}</local-command-stdout>'
            return transcript_line(content)

        lines = [effort_msg('max'), transcript_line('do something'), effort_msg('high')]
        transcript.write_text('\n'.join(lines) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort == 'high'

    def test_non_string_content(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        entry = json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result'}]}})
        transcript.write_text(entry + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort is None

    def test_reads_beyond_first_chunk(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        effort_content = '<local-command-stdout>Set effort level to max</local-command-stdout>'
        padding = [transcript_line('x' * 200) for _ in range(200)]
        lines = [transcript_line(effort_content), *padding]
        transcript.write_text('\n'.join(lines) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort == 'max'

    def test_returns_latest_ts(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        entry = json.dumps({
            'type': 'user',
            'timestamp': '2026-03-15T11:00:00Z',
            'message': {'content': '<local-command-stdout>Set effort level to max</local-command-stdout>'},
        })
        transcript.write_text(entry + '\n')
        effort, ts, _ = scan_transcript_effort(str(transcript))
        assert effort == 'max'
        assert ts == '2026-03-15T11:00:00Z'

    def test_since_ts_skips_old(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_entry = json.dumps({
            'type': 'user',
            'timestamp': '2026-03-15T10:00:00Z',
            'message': {'content': '<local-command-stdout>Set effort level to low</local-command-stdout>'},
        })
        new_entry = json.dumps({
            'type': 'user',
            'timestamp': '2026-03-15T11:00:00Z',
            'message': {'content': '<local-command-stdout>Set effort level to max</local-command-stdout>'},
        })
        transcript.write_text(old_entry + '\n' + new_entry + '\n')
        effort, ts, _ = scan_transcript_effort(str(transcript), '2026-03-15T10:00:00Z')
        assert effort == 'max'
        assert ts == '2026-03-15T11:00:00Z'

    def test_invalid_json_skipped(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        content = '<local-command-stdout>Set effort level to high</local-command-stdout>'
        lines = [transcript_line(content), '{bad json']
        transcript.write_text('\n'.join(lines) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort == 'high'

    def test_synthetic_invalidates_effort(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        effort_entry = transcript_line('<local-command-stdout>Set effort level to high</local-command-stdout>')
        synthetic_entry = json.dumps({
            'type': 'assistant',
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'No response requested.'}]},
        })
        lines = [effort_entry, synthetic_entry]
        transcript.write_text('\n'.join(lines) + '\n')
        effort, _, _ = scan_transcript_effort(str(transcript))
        assert effort is None


class TestResolveEffort:
    def test_scan_effort_returned(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        content = '<local-command-stdout>Set effort level to max</local-command-stdout>'
        transcript.write_text(transcript_line(content) + '\n')
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path / 'cache'):
            result = resolve_effort(str(transcript), 'sess-1')
        assert result == 'max'

    def test_cached_effort_used_when_no_transcript(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (cache_dir / 'sess-1.json').write_text(json.dumps({'effort': 'high', '_v': app_version}))
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir):
            result = resolve_effort(None, 'sess-1')
        assert result == 'high'

    def test_no_effort_no_cache_falls_back_to_settings(self, tmp_path: Path) -> None:
        with (
            mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path),
            mock.patch('claude_vibeline.statusline.read_settings_effort', return_value='medium?'),
        ):
            result = resolve_effort(None, 'sess-new')
        assert result == 'medium?'

    def test_no_session_id_falls_back_to_settings(self) -> None:
        with mock.patch('claude_vibeline.statusline.read_settings_effort', return_value='medium?'):
            result = resolve_effort(None, None)
        assert result == 'medium?'

    def test_scan_beats_cached_effort(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (cache_dir / 'sess-1.json').write_text(json.dumps({'effort': 'high'}))
        transcript = tmp_path / 'session.jsonl'
        content = '<local-command-stdout>Set effort level to max</local-command-stdout>'
        transcript.write_text(transcript_line(content) + '\n')
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir):
            result = resolve_effort(str(transcript), 'sess-1')
        assert result == 'max'

    def test_incremental_scan(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_entry = json.dumps({
            'type': 'user',
            'timestamp': '2026-03-15T10:00:00Z',
            'message': {'content': '<local-command-stdout>Set effort level to low</local-command-stdout>'},
        })
        new_entry = json.dumps({
            'type': 'user',
            'timestamp': '2026-03-15T11:00:00Z',
            'message': {'content': '<local-command-stdout>Set effort level to max</local-command-stdout>'},
        })
        transcript.write_text(old_entry + '\n' + new_entry + '\n')
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (cache_dir / 'sess-1.json').write_text(json.dumps({'effort': 'low', 'effort_ts': '2026-03-15T10:00:00Z'}))
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir):
            result = resolve_effort(str(transcript), 'sess-1')
        assert result == 'max'

    def test_new_entries_preserve_cached_effort(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (cache_dir / 'sess-1.json').write_text(
            json.dumps({'effort': 'high', 'effort_ts': '2026-03-15T10:00:00Z', '_v': app_version})
        )
        transcript = tmp_path / 'session.jsonl'
        entry = json.dumps({'type': 'user', 'timestamp': '2026-03-15T11:00:00Z', 'message': {'content': 'hello'}})
        transcript.write_text(entry + '\n')
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir):
            result = resolve_effort(str(transcript), 'sess-1')
        assert result == 'high'

    def test_synthetic_clears_cached_effort(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (cache_dir / 'sess-1.json').write_text(json.dumps({'effort': 'high'}))
        transcript = tmp_path / 'session.jsonl'
        synthetic = json.dumps({
            'type': 'assistant',
            'timestamp': '2026-03-15T11:00:00Z',
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'No response requested.'}]},
        })
        transcript.write_text(synthetic + '\n')
        with (
            mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir),
            mock.patch('claude_vibeline.statusline.read_settings_effort', return_value='medium?'),
        ):
            result = resolve_effort(str(transcript), 'sess-1')
        assert result == 'medium?'


class TestReadSettingsEffort:
    def test_valid_settings(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'effortLevel': 'low'}))
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            assert read_settings_effort() == 'low?'

    def test_missing_file(self, tmp_path: Path) -> None:
        with mock.patch.object(Path, 'expanduser', return_value=tmp_path / 'nonexistent.json'):
            assert read_settings_effort() == 'medium?'

    def test_invalid_json(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text('{bad')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            assert read_settings_effort() == 'medium?'

    def test_no_effort_key(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'model': 'opus'}))
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            assert read_settings_effort() == 'medium?'


class TestSessionCache:
    def test_read_missing(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            assert read_session_cache('missing') == {}

    def test_write_and_read(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            write_session_cache('sess-1', {'effort': 'high'})
            assert read_session_cache('sess-1')['effort'] == 'high'

    def test_write_overwrites(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            write_session_cache('sess-1', {'effort': 'high'})
            write_session_cache('sess-1', {'effort': 'low'})
            assert read_session_cache('sess-1')['effort'] == 'low'

    def test_version_mismatch_ignored(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            (tmp_path / 'sess-1.json').write_text(json.dumps({'effort': 'high', '_v': '0.0.0'}))
            assert read_session_cache('sess-1') == {}

    def test_write_oserror_silenced(self, tmp_path: Path) -> None:
        with (
            mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path),
            mock.patch.object(Path, 'mkdir', side_effect=OSError),
        ):
            write_session_cache('sess-fail', {'effort': 'high'})

    def test_returns_path(self) -> None:
        result = session_cache_dir()
        assert result.name == 'sessions'
        assert 'claude-vibeline' in str(result)

    def test_read_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / 'bad.json').write_text('{bad json')
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            assert read_session_cache('bad') == {}

    def test_write_merges_fields(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            write_session_cache('sess-1', {'effort': 'high'})
            write_session_cache('sess-1', {'last_user_ts': 1000.0})
            cached = read_session_cache('sess-1')
            assert cached['effort'] == 'high'
            assert cached['last_user_ts'] == 1000.0  # noqa: RUF069 - exact roundtrip via JSON


class TestCleanupSessionCache:
    def test_removes_old_files(self, tmp_path: Path) -> None:
        old = tmp_path / 'old-session'
        old.write_text('high')
        os.utime(old, (0, 0))
        recent = tmp_path / 'recent-session'
        recent.write_text('low')
        cleanup_session_cache(tmp_path)
        assert not old.exists()
        assert recent.exists()

    def test_oserror_silenced(self) -> None:
        cleanup_session_cache(Path('/nonexistent/path'))


class TestReadOauthToken:
    def test_valid_credentials(self, tmp_path: Path) -> None:
        creds = tmp_path / '.credentials.json'
        creds.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'tok123'}}))
        with mock.patch.object(Path, 'expanduser', return_value=creds):
            assert read_oauth_token() == 'tok123'

    def test_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / 'nonexistent.json'
        with mock.patch.object(Path, 'expanduser', return_value=missing):
            assert read_oauth_token() is None

    def test_invalid_json(self, tmp_path: Path) -> None:
        creds = tmp_path / '.credentials.json'
        creds.write_text('{bad')
        with mock.patch.object(Path, 'expanduser', return_value=creds):
            assert read_oauth_token() is None

    def test_macos_keychain(self, tmp_path: Path) -> None:
        missing = tmp_path / 'nonexistent.json'
        keychain_data = json.dumps({'claudeAiOauth': {'accessToken': 'keychain_tok'}})
        with (
            mock.patch.object(Path, 'expanduser', return_value=missing),
            mock.patch('claude_vibeline.statusline.sys.platform', 'darwin'),
            mock.patch('claude_vibeline.statusline.sp.run') as mock_run,
        ):
            mock_run.return_value = mock.Mock(stdout=keychain_data + '\n')
            assert read_oauth_token() == 'keychain_tok'

    def test_macos_keychain_error(self, tmp_path: Path) -> None:
        missing = tmp_path / 'nonexistent.json'
        with (
            mock.patch.object(Path, 'expanduser', return_value=missing),
            mock.patch('claude_vibeline.statusline.sys.platform', 'darwin'),
            mock.patch('claude_vibeline.statusline.sp.run', side_effect=sp.CalledProcessError(1, 'security')),
        ):
            assert read_oauth_token() is None


class TestTokenFromEntry:
    def test_valid_no_expiry(self) -> None:
        assert token_from_entry({'accessToken': 'tok'}) == 'tok'

    def test_no_access_token(self) -> None:
        assert token_from_entry({}) is None

    def test_not_expired(self) -> None:
        future = int(time.time()) + 3600
        assert token_from_entry({'accessToken': 'tok', 'expiresAt': future}) == 'tok'

    def test_expired(self) -> None:
        past = int(time.time()) - 60
        assert token_from_entry({'accessToken': 'tok', 'expiresAt': past}) is None

    def test_expired_token_in_read_oauth(self, tmp_path: Path) -> None:
        past = int(time.time()) - 60
        creds = tmp_path / '.credentials.json'
        creds.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'tok', 'expiresAt': past}}))
        with mock.patch.object(Path, 'expanduser', return_value=creds):
            assert read_oauth_token() is None


class TestWriteUsageCache:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        cache = tmp_path / 'cache' / 'usage.json'
        data: UsageData = {'five_hour': {'utilization': 10}}
        write_usage_cache(cache, data)
        written = json.loads(cache.read_text())
        assert written['five_hour'] == {'utilization': 10}
        assert '_ts' in written

    def test_handles_oserror(self) -> None:
        cache = Path('/nonexistent/deeply/nested/usage.json')
        with mock.patch('claude_vibeline.statusline.Path.mkdir', side_effect=OSError):
            write_usage_cache(cache, {'five_hour': {'utilization': 0}})


class TestFetchUsage:
    @responses.activate
    def test_successful_api_call(self, tmp_path: Path) -> None:
        api_data = {'five_hour': {'utilization': 42, 'resets_at': '2099-01-01T00:00:00+00:00'}}
        responses.add(responses.GET, USAGE_URL, json=api_data, status=200)

        cache = tmp_path / 'usage.json'
        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result == api_data
        assert stale_ts is None
        assert cache.exists()

    @responses.activate
    def test_corrupt_cache_refetches(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        cache.write_text('{bad json')

        fresh_data = {'five_hour': {'utilization': 55}}
        responses.add(responses.GET, USAGE_URL, json=fresh_data, status=200)

        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result == fresh_data
        assert stale_ts is None

    def test_cached_response_within_ttl(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        cached = {'five_hour': {'utilization': 10}, '_ts': time.time(), '_v': app_version}
        cache.write_text(json.dumps(cached))

        with mock.patch('claude_vibeline.statusline.cache_path', return_value=cache):
            result, stale_ts = fetch_usage()

        assert result is not None
        assert result['five_hour']['utilization'] == 10
        assert stale_ts is None

    @responses.activate
    def test_stale_cache_refetches(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        stale = {'five_hour': {'utilization': 10}, '_ts': time.time() - CACHE_TTL_SECONDS - 1}
        cache.write_text(json.dumps(stale))

        fresh_data = {'five_hour': {'utilization': 99}}
        responses.add(responses.GET, USAGE_URL, json=fresh_data, status=200)

        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result == fresh_data
        assert stale_ts is None

    def test_no_token(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value=None),
        ):
            result, stale_ts = fetch_usage()

        assert result is None
        assert stale_ts is None

    @responses.activate
    def test_api_error_returns_none_and_caches_negative(self, tmp_path: Path) -> None:
        responses.add(responses.GET, USAGE_URL, status=500)

        cache = tmp_path / 'usage.json'
        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result is None
        assert stale_ts is None
        assert cache.exists()

    @responses.activate
    def test_invalid_json_response(self, tmp_path: Path) -> None:
        responses.add(responses.GET, USAGE_URL, body='not json', status=200, content_type='text/plain')

        cache = tmp_path / 'usage.json'
        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result is None
        assert stale_ts is None

    @responses.activate
    def test_stale_cache_on_api_error(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        old_data = {'five_hour': {'utilization': 10}, '_ts': time.time() - CACHE_TTL_SECONDS - 1, '_v': app_version}
        cache.write_text(json.dumps(old_data))

        responses.add(responses.GET, USAGE_URL, status=500)

        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result is not None
        assert result['five_hour']['utilization'] == 10
        assert isinstance(stale_ts, float)

    def test_stale_cache_on_no_token(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        old_data = {'five_hour': {'utilization': 30}, '_ts': time.time() - CACHE_TTL_SECONDS - 1, '_v': app_version}
        cache.write_text(json.dumps(old_data))

        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value=None),
        ):
            result, stale_ts = fetch_usage()

        assert result is not None
        assert result['five_hour']['utilization'] == 30
        assert isinstance(stale_ts, float)


class TestFormatContextSize:
    def test_200k(self) -> None:
        assert format_context_size(200_000) == '200k'

    def test_1m(self) -> None:
        assert format_context_size(1_000_000) == '1M'

    def test_128k(self) -> None:
        assert format_context_size(128_000) == '128k'

    def test_1_5m(self) -> None:
        assert format_context_size(1_500_000) == '1.5M'


class TestVisibleLen:
    def test_plain_text(self) -> None:
        assert visible_len('hello') == 5

    def test_ansi_stripped(self) -> None:
        assert visible_len('\033[38;5;209mhello\033[0m') == 5

    def test_empty(self) -> None:
        assert visible_len('') == 0

    def test_nbsp_counted(self) -> None:
        assert visible_len(f'a{NBSP}b') == 3

    def test_bar_visible_len(self) -> None:
        result = bar(50, 8)
        assert visible_len(result) == 8


class TestWrapParts:
    def test_single_line_no_wrap(self) -> None:
        parts = ['aaa', 'bbb']
        result = wrap_parts(parts, 120)
        assert '\n' not in result
        assert 'aaa' in result
        assert 'bbb' in result

    def test_wraps_when_exceeding_columns(self) -> None:
        parts = ['a' * 40, 'b' * 40, 'c' * 40]
        result = wrap_parts(parts, 80)
        lines = result.split('\n')
        assert len(lines) >= 2

    def test_trailing_separator_on_wrapped_lines(self) -> None:
        parts = ['a' * 40, 'b' * 40, 'c' * 10]
        result = wrap_parts(parts, 50)
        lines = result.split('\n')
        assert len(lines) >= 2
        sep_plain = ANSI_RE.sub('', SEP).strip()
        for line in lines[:-1]:
            plain = ANSI_RE.sub('', line).rstrip()
            assert plain.endswith(sep_plain)
        last_plain = ANSI_RE.sub('', lines[-1]).rstrip()
        assert not last_plain.endswith(sep_plain)

    def test_no_trailing_separator_single_line(self) -> None:
        parts = ['aaa', 'bbb']
        result = wrap_parts(parts, 120)
        sep_plain = ANSI_RE.sub('', SEP).strip()
        plain = ANSI_RE.sub('', result).rstrip()
        assert not plain.endswith(sep_plain)

    def test_empty_parts(self) -> None:
        assert not wrap_parts([], 80)

    def test_single_part(self) -> None:
        result = wrap_parts(['hello world'], 80)
        assert result == 'hello world'

    def test_spaces_preserved(self) -> None:
        result = wrap_parts(['a b c'], 80)
        assert 'a b c' in result

    def test_each_line_within_columns(self) -> None:
        parts = ['a' * 20, 'b' * 20, 'c' * 20, 'd' * 20]
        result = wrap_parts(parts, 50)
        for line in result.split('\n'):
            assert visible_len(line) <= 50 + visible_len(SEP)

    def test_wide_part_not_split(self) -> None:
        parts = ['a' * 100]
        result = wrap_parts(parts, 50)
        assert '\n' not in result


STDIN_DATA = {
    'workspace': {'project_dir': '/home/user/my-project'},
    'model': {'display_name': 'Opus 4.6'},
    'context_window': {'used_percentage': 42.3},
}


def run_main(
    stdin_data: StdinData | dict[str, Any] | None = None, argv: list[str] | None = None, effort: str | None = 'high'
) -> str:
    import claude_vibeline.statusline as _mod  # noqa: PLC0415

    data = stdin_data or STDIN_DATA
    argv = argv or ['claude-vibeline']
    stdin_buf = io.BytesIO(json.dumps(data).encode())
    stdout_buf = io.BytesIO()
    fake_stdin = io.TextIOWrapper(stdin_buf, encoding='utf-8')
    fake_stdout = io.TextIOWrapper(stdout_buf, encoding='utf-8')
    with (
        mock.patch('sys.argv', argv),
        mock.patch.object(_mod.sys, 'stdin', fake_stdin),
        mock.patch.object(_mod.sys, 'stdout', fake_stdout),
        mock.patch('claude_vibeline.statusline.resolve_effort', return_value=effort),
    ):
        main()
        _mod.sys.stdout.flush()
        return stdout_buf.getvalue().decode('utf-8')


class TestMain:
    def test_full_pipeline_with_usage(self) -> None:
        usage_data = {
            'five_hour': {'utilization': 19, 'resets_at': '2099-01-01T00:00:00+00:00'},
            'seven_day': {'utilization': 3, 'resets_at': '2099-01-01T00:00:00+00:00'},
        }
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(usage_data, None)):
            output = run_main()
        assert 'my-project' in output
        assert 'Opus' in output
        assert '42%' in output
        assert '19%' in output
        assert '3%' in output
        assert '\u2265' not in output

    def test_no_usage_data(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main()
        assert 'my-project' in output
        assert 'Opus' in output
        assert '42%' in output

    def test_no_project_flag(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(argv=['claude-vibeline', '--no-project'])
        assert 'my-project' not in output
        assert 'Opus' in output

    def test_no_model_flag(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(argv=['claude-vibeline', '--no-model'])
        assert 'my-project' in output
        assert 'Opus' not in output

    def test_empty_model_name(self) -> None:
        data = {**STDIN_DATA, 'model': {'display_name': ''}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'Unknown' in output

    def test_context_window_size_shown(self) -> None:
        data = {**STDIN_DATA, 'context_window': {'used_percentage': 42.3, 'context_window_size': 200_000}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert '200k' in output
        assert '42%' in output

    def test_context_window_size_1m(self) -> None:
        data = {**STDIN_DATA, 'context_window': {'used_percentage': 10.0, 'context_window_size': 1_000_000}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert '1M' in output

    def test_context_window_size_absent(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main()
        assert '200k' not in output
        assert '1M' not in output
        assert '42%' in output

    def test_no_context_flag(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(argv=['claude-vibeline', '--no-context'])
        assert '42%' not in output

    def test_no_usage_flag(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage') as mock_fetch:
            output = run_main(argv=['claude-vibeline', '--no-usage'])
        mock_fetch.assert_not_called()
        assert 'my-project' in output

    def test_bar_width_flag(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(argv=['claude-vibeline', '--bar-width', '4'])
        assert output.count(FILL) + output.count(EMPTY) == 4

    def test_extra_usage_shown(self) -> None:
        usage_data = {
            'five_hour': {'utilization': 10, 'resets_at': '2099-01-01T00:00:00+00:00'},
            'extra_usage': {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000},
        }
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(usage_data, None)):
            output = run_main()
        assert 'extra' in output
        assert '2.50' in output

    def test_max_effort(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(effort='max')
        assert '(max)' in output

    def test_haiku_no_effort(self) -> None:
        data = {**STDIN_DATA, 'model': {'display_name': 'Haiku 4.5'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'Haiku' in output
        assert '(' not in output

    def test_stale_within_window(self) -> None:
        usage_data = {'five_hour': {'utilization': 19, 'resets_at': '2099-01-01T00:00:00+00:00'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(usage_data, time.time() - 120)):
            output = run_main()
        assert '\u2265' in output
        assert '19%' in output
        assert '--' not in output

    @freeze_time('2026-03-07T12:00:00Z')
    def test_stale_past_reset(self) -> None:
        usage_data = {'five_hour': {'utilization': 19, 'resets_at': '2026-03-07T10:00:00+00:00'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(usage_data, time.time() - 120)):
            output = run_main()
        assert '?' in output
        assert '\u2265' not in output

    def test_dunder_main(self) -> None:
        with mock.patch('claude_vibeline.statusline.main') as mock_main:
            runpy.run_module('claude_vibeline', run_name='__main__')
        mock_main.assert_called_once()

    def test_corrupt_stdin_outputs_nothing(self) -> None:
        import claude_vibeline.statusline as _mod  # noqa: PLC0415

        stdin_buf = io.BytesIO(b'{bad json')
        stdout_buf = io.BytesIO()
        fake_stdin = io.TextIOWrapper(stdin_buf, encoding='utf-8')
        fake_stdout = io.TextIOWrapper(stdout_buf, encoding='utf-8')
        with (
            mock.patch('sys.argv', ['claude-vibeline']),
            mock.patch.object(_mod.sys, 'stdin', fake_stdin),
            mock.patch.object(_mod.sys, 'stdout', fake_stdout),
        ):
            main()
            _mod.sys.stdout.flush()
            assert stdout_buf.getvalue() == b''

    def test_cache_section_shown(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        data = {**STDIN_DATA, 'transcript_path': str(transcript)}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'cache' in output
        assert '\u2713' in output

    def test_cache_expired_shown(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        transcript.write_text(_user(old_ts) + '\n')

        data = {**STDIN_DATA, 'transcript_path': str(transcript)}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'cache' in output
        assert '\u2717' in output

    def test_no_cache_flag(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        data = {**STDIN_DATA, 'transcript_path': str(transcript)}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data, argv=['claude-vibeline', '--no-cache'])
        assert 'cache' not in output

    def test_debug_logs_to_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'logs' / 'debug.log'
        with (
            mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)),
            mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file),
        ):
            run_main(argv=['claude-vibeline', '--debug'])
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert 'my-project' in entry['output']
        assert '\033[' not in entry['output']
        assert entry['effort'] == 'high'

    def test_fallback_effort_shows_question_mark(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(effort='high?')
        assert 'Opus 4.6' in output
        assert '(high?)' in output


class TestWriteDebugLog:
    def test_appends_to_existing(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        log_file.write_text('{"old": true}\n')
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        lines = log_file.read_text(encoding='utf-8').strip().splitlines()
        assert len(lines) == 2
        entry = json.loads(lines[1])
        assert entry['output'] == 'test output'

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'nested' / 'dir' / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        assert log_file.exists()

    def test_truncates_large_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        log_file.write_bytes(b'x' * (DEBUG_LOG_MAX_BYTES + 1))
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        size = log_file.stat().st_size
        assert size < DEBUG_LOG_MAX_BYTES

    def test_truncation_preserves_jsonl_structure(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        lines: list[str] = []
        while sum(len(ln) for ln in lines) < DEBUG_LOG_MAX_BYTES + 1000:
            lines.append(json.dumps({'i': len(lines), 'pad': 'x' * 200}) + '\n')
        log_file.write_text(''.join(lines))
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        content = log_file.read_text(encoding='utf-8')
        for line in content.strip().splitlines():
            json.loads(line)

    def test_jsonl_format_with_args(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True, bar_width=12)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['args']['bar_width'] == 12
        assert 'ts' in entry
        assert entry['output'] == 'test output'

    def test_session_id_from_transcript(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        stdin_data: StdinData = {'transcript_path': '/home/user/.claude/sessions/abc-123-def.jsonl'}
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args, stdin_data=stdin_data)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['session'] == 'abc-123-def'

    def test_no_session_without_transcript(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['session'] is None

    def test_effort_in_entry(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args, effort='high')
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['effort'] == 'high'

    def test_oserror_silenced(self) -> None:
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', side_effect=OSError):
            write_debug_log('test output', args)

    def test_usage_data_in_entry(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        usage: UsageData = {'five_hour': {'utilization': 42}}
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args, usage_data=usage, stale_ts=123.0)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['usage']['five_hour']['utilization'] == 42
        assert entry['stale_ts'] == 123

    def test_nbsp_replaced_in_output(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log(f'a{NBSP}b', args)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['output'] == 'a b'


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
    def test_no_transcript(self) -> None:
        assert prompt_cache_section(None) == (None, None)

    def test_warm_cache(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result, _ = prompt_cache_section(str(transcript))
        assert result is not None
        assert 'cache' in result
        assert '\u2713' in result
        assert 'm' in result

    def test_warm_cache_low(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = (datetime.now(UTC) - timedelta(seconds=PROMPT_CACHE_TTL - CACHE_LOW_THRESHOLD + 10)).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result, _ = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u26a0' in result
        assert '\u2713' not in result

    def test_expired_cache(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_ts = (datetime.now(UTC) - timedelta(seconds=PROMPT_CACHE_TTL + 10)).isoformat()
        transcript.write_text(_user(old_ts) + '\n')

        result, _ = prompt_cache_section(str(transcript))
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

        result, _ = prompt_cache_section(str(transcript))
        assert result is not None
        assert '!' in result
        assert '\u2713' in result
        assert '\u2717' not in result

    def test_no_recache_indicator_when_no_gap(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        now = datetime.now(UTC)
        user_ts = (now - timedelta(seconds=60)).isoformat()
        recent_ts = now.isoformat()
        lines = [_user(user_ts), _assistant(user_ts), _tool_result(recent_ts)]
        transcript.write_text('\n'.join(lines) + '\n')

        result, _ = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u2713' in result
        assert '!' not in result
        assert '\u2717' not in result

    def test_no_recache_indicator_single_message(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result, _ = prompt_cache_section(str(transcript))
        assert result is not None
        assert '!' not in result

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

        result, _ = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u2713' in result
        assert '!' not in result

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

        result, _ = prompt_cache_section(str(transcript))
        assert result is not None
        assert '!' in result

    def test_no_user_messages(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_assistant('2026-03-07T10:00:00Z') + '\n')
        assert prompt_cache_section(str(transcript)) == (None, None)

    def test_caches_last_user_ts(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir):
            _, last_ts = prompt_cache_section(str(transcript), 'sess-1')
            assert last_ts is not None
            cached = read_session_cache('sess-1')
            assert cached['last_user_ts'] == last_ts

    def test_falls_back_to_cached_last_user_ts(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_assistant('2026-03-07T10:00:00Z') + '\n')
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        cached_ts = time.time() - 60
        (cache_dir / 'sess-1.json').write_text(json.dumps({'last_user_ts': cached_ts, '_v': app_version}))
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir):
            result, last_ts = prompt_cache_section(str(transcript), 'sess-1')
        assert last_ts == cached_ts
        assert result is not None
        assert '\u2713' in result


class TestToggleSettingsSpace:
    def _settings_json(self, cmd: str) -> str:
        return json.dumps({'statusLine': {'type': 'command', 'command': cmd}}, indent=2) + '\n'

    def _read_cmd(self, settings: Path) -> str:
        return json.loads(settings.read_text(encoding='utf-8'))['statusLine']['command']

    def test_adds_trailing_space(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text(self._settings_json('uv run foo'), encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
        assert self._read_cmd(settings) == 'uv run foo '

    def test_removes_trailing_space(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text(self._settings_json('uv run foo '), encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
        assert self._read_cmd(settings) == 'uv run foo'

    def test_preserves_other_content(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        data = {'model': 'opus', 'statusLine': {'type': 'command', 'command': 'cmd'}, 'debug': True}
        settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
        result = json.loads(settings.read_text())
        assert result['model'] == 'opus'
        assert result['debug'] is True
        assert result['statusLine']['command'] == 'cmd '

    def test_no_status_line(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text('{"model": "opus"}\n', encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
        assert json.loads(settings.read_text()) == {'model': 'opus'}

    def test_only_modifies_status_line_command(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        data = {'hooks': {'command': 'hook cmd'}, 'statusLine': {'type': 'command', 'command': 'sl cmd'}}
        settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
        result = json.loads(settings.read_text())
        assert result['hooks']['command'] == 'hook cmd'
        assert result['statusLine']['command'] == 'sl cmd '

    def test_missing_file(self, tmp_path: Path) -> None:
        with mock.patch.object(Path, 'expanduser', return_value=tmp_path / 'nonexistent.json'):
            toggle_settings_space()


class TestIsLockOwner:
    def test_owns_lock(self, tmp_path: Path) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': 'abc123', '_v': app_version}))
        assert is_lock_owner(lock, 'abc123')

    def test_different_token(self, tmp_path: Path) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': 'abc123'}))
        assert not is_lock_owner(lock, 'other')

    def test_no_lock_file(self, tmp_path: Path) -> None:
        assert not is_lock_owner(tmp_path / 'nonexistent.lock', 'abc')

    def test_corrupt_lock(self, tmp_path: Path) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text('{bad')
        assert not is_lock_owner(lock, 'abc')


class TestRunRefreshLoop:
    TOKEN: ClassVar[str] = 'test-token'

    def test_already_expired(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': self.TOKEN, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        toggles: list[int] = []
        monkeypatch.setattr('claude_vibeline.statusline.toggle_settings_space', lambda: toggles.append(1))
        with mock.patch('time.sleep'):
            run_refresh_loop(time.time() - 1, self.TOKEN)
        assert len(toggles) == 0
        assert not lock.exists()

    def test_exits_when_lock_taken_mid_loop(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': self.TOKEN, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        toggles: list[int] = []

        def toggle_and_steal_lock() -> None:
            toggles.append(1)
            lock.write_text(json.dumps({'token': 'new-owner'}))

        monkeypatch.setattr('claude_vibeline.statusline.toggle_settings_space', toggle_and_steal_lock)
        times = iter([100.0, 130.0])
        monkeypatch.setattr(time, 'time', lambda: next(times))
        with mock.patch('time.sleep'):
            run_refresh_loop(200.0, self.TOKEN)
        assert len(toggles) == 1
        assert lock.exists()

    def test_loop_toggles_and_cleans_up(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': self.TOKEN, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        toggles: list[int] = []
        monkeypatch.setattr('claude_vibeline.statusline.toggle_settings_space', lambda: toggles.append(1))
        # time.time() called once per iteration (remaining calc)
        # iter 1: 150-100=50>0 → toggle; iter 2: 150-130=20>0 → toggle
        # iter 3: 150-160<0 → break (no extra toggle)
        times = iter([100.0, 130.0, 160.0])
        monkeypatch.setattr(time, 'time', lambda: next(times))
        with mock.patch('time.sleep'):
            run_refresh_loop(150.0, self.TOKEN)
        assert len(toggles) == 2
        assert not lock.exists()


class TestSpawnCacheUpdater:
    def test_spawns_and_writes_lock(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: tmp_path / 'refresh.lock')
        mock_proc = mock.MagicMock(pid=12345)
        with mock.patch('claude_vibeline.statusline.sp.Popen', return_value=mock_proc) as popen:
            spawn_cache_updater(time.time() + 300)
            popen.assert_called_once()
        lock = tmp_path / 'refresh.lock'
        assert lock.exists()
        data = json.loads(lock.read_text())
        assert 'token' in data
        assert 'expiry' in data

    def test_skips_when_same_expiry(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        expiry = time.time() + 300
        lock.write_text(json.dumps({'token': 'old', 'expiry': expiry, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        with mock.patch('claude_vibeline.statusline.sp.Popen') as popen:
            spawn_cache_updater(expiry)
            popen.assert_not_called()
        assert json.loads(lock.read_text())['token'] == 'old'

    def test_skips_when_expiry_earlier(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        expiry = time.time() + 300
        lock.write_text(json.dumps({'token': 'old', 'expiry': expiry, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        with mock.patch('claude_vibeline.statusline.sp.Popen') as popen:
            spawn_cache_updater(expiry - 5)
            popen.assert_not_called()

    def test_spawns_when_expiry_changed(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        old_expiry = time.time() + 100
        new_expiry = time.time() + 400
        lock.write_text(json.dumps({'token': 'old', 'expiry': old_expiry}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        mock_proc = mock.MagicMock(pid=55555)
        with mock.patch('claude_vibeline.statusline.sp.Popen', return_value=mock_proc):
            spawn_cache_updater(new_expiry)
        data = json.loads(lock.read_text())
        assert data['token'] != 'old'
        assert data['expiry'] == new_expiry

    def test_spawns_when_no_lock(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: tmp_path / 'refresh.lock')
        mock_proc = mock.MagicMock(pid=12345)
        with mock.patch('claude_vibeline.statusline.sp.Popen', return_value=mock_proc):
            spawn_cache_updater(time.time() + 300)
        assert (tmp_path / 'refresh.lock').exists()

    def test_spawns_when_corrupt_lock(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text('{bad')
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        mock_proc = mock.MagicMock(pid=12345)
        with mock.patch('claude_vibeline.statusline.sp.Popen', return_value=mock_proc):
            spawn_cache_updater(time.time() + 300)
        assert 'token' in json.loads(lock.read_text())

    def test_handles_oserror(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: tmp_path / 'refresh.lock')
        with mock.patch('claude_vibeline.statusline.sp.Popen', side_effect=OSError):
            spawn_cache_updater(time.time() + 300)
        assert not (tmp_path / 'refresh.lock').exists()


class TestRefreshIntegration:
    """
    Integration tests for the cache refresh mechanism.

    Uses real files and threads instead of mocks.
    """

    def test_toggle_only_modifies_statusline_command(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        data = {
            'hooks': {'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'bash hook.sh'}]}]},
            'statusLine': {'type': 'command', 'command': 'run statusline'},
            'model': 'opus',
        }
        settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
        result = json.loads(settings.read_text(encoding='utf-8'))
        assert result['statusLine']['command'] == 'run statusline '
        assert result['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'] == 'bash hook.sh'

    def test_toggle_is_reversible(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        original = {'statusLine': {'type': 'command', 'command': 'my cmd'}}
        settings.write_text(json.dumps(original, indent=2) + '\n', encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
            assert json.loads(settings.read_text(encoding='utf-8'))['statusLine']['command'] == 'my cmd '
            toggle_settings_space()
            assert json.loads(settings.read_text(encoding='utf-8'))['statusLine']['command'] == 'my cmd'

    def test_refresh_loop_toggles_settings(self, tmp_path: Path, monkeypatch: Any) -> None:
        settings = tmp_path / 'settings.json'
        data = {'statusLine': {'type': 'command', 'command': 'cmd'}}
        settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        token = 'test-token'
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': token, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        monkeypatch.setattr('claude_vibeline.statusline.REFRESH_INTERVAL', 0.1)
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            t = threading.Thread(target=run_refresh_loop, args=(time.time() + 0.5, token))
            t.start()
            t.join(timeout=5)
        result = json.loads(settings.read_text(encoding='utf-8'))
        assert result['statusLine']['command'] in {'cmd', 'cmd '}
        assert not lock.exists()

    def test_cooperative_shutdown_on_token_change(self, tmp_path: Path, monkeypatch: Any) -> None:
        settings = tmp_path / 'settings.json'
        data = {'statusLine': {'type': 'command', 'command': 'cmd'}}
        settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        token = 'old-token'
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': token, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        monkeypatch.setattr('claude_vibeline.statusline.REFRESH_INTERVAL', 0.2)
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            t = threading.Thread(target=run_refresh_loop, args=(time.time() + 10, token))
            t.start()
            time.sleep(0.3)
            assert t.is_alive()
            lock.write_text(json.dumps({'token': 'new-owner'}))
            t.join(timeout=3)
        assert not t.is_alive()

    def test_same_expiry_does_not_respawn(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        expiry = time.time() + 300
        lock.write_text(json.dumps({'token': 'existing', 'expiry': expiry, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        with mock.patch('claude_vibeline.statusline.sp.Popen') as popen:
            spawn_cache_updater(expiry)
            popen.assert_not_called()

    def test_different_expiry_does_respawn(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        old_expiry = time.time() + 100
        lock.write_text(json.dumps({'token': 'old', 'expiry': old_expiry}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        mock_proc = mock.MagicMock(pid=55555)
        with mock.patch('claude_vibeline.statusline.sp.Popen', return_value=mock_proc):
            spawn_cache_updater(time.time() + 400)
        data = json.loads(lock.read_text())
        assert data['token'] != 'old'

    def test_lock_cleaned_after_expiry(self, tmp_path: Path, monkeypatch: Any) -> None:
        settings = tmp_path / 'settings.json'
        data = {'statusLine': {'type': 'command', 'command': 'cmd'}}
        settings.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        token = 'test-token'
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': token, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.statusline.refresh_lock_path', lambda: lock)
        monkeypatch.setattr('claude_vibeline.statusline.REFRESH_INTERVAL', 0.1)
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            t = threading.Thread(target=run_refresh_loop, args=(time.time() + 0.2, token))
            t.start()
            t.join(timeout=5)
        assert not lock.exists()


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


class TestWrapPartsAnsi:
    def test_ansi_parts_fit_on_one_line(self) -> None:
        parts = [f'{ORANGE}hello{RESET}', f'{PERC}world{RESET}']
        result = wrap_parts(parts, 40)
        assert '\n' not in result

    def test_ansi_parts_wrap_at_visible_width(self) -> None:
        p1 = f'{ORANGE}{"a" * 10}{RESET}'
        p2 = f'{PERC}{"b" * 10}{RESET}'
        result = wrap_parts([p1, p2], 15)
        assert '\n' in result


class TestProjectNameEdgeCases:
    def test_project_dir_dot(self) -> None:
        data = {**STDIN_DATA, 'workspace': {'project_dir': '.'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert '.' not in ANSI_RE.sub('', output).split()

    def test_project_dir_root(self) -> None:
        data = {**STDIN_DATA, 'workspace': {'project_dir': '/'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'Opus' in output

    def test_missing_workspace(self) -> None:
        data: StdinData = {'model': {'display_name': 'Opus 4.6'}, 'context_window': {'used_percentage': 10.0}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'Opus' in output

    def test_empty_workspace(self) -> None:
        data = {**STDIN_DATA, 'workspace': {}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'Opus' in output


class TestIndividualBucketFlags:
    FULL_USAGE: ClassVar[UsageData] = {
        'five_hour': {'utilization': 10, 'resets_at': '2099-01-01T00:00:00+00:00'},
        'seven_day': {'utilization': 20, 'resets_at': '2099-01-01T00:00:00+00:00'},
        'seven_day_opus': {'utilization': 30, 'resets_at': '2099-01-01T00:00:00+00:00'},
        'seven_day_sonnet': {'utilization': 40, 'resets_at': '2099-01-01T00:00:00+00:00'},
        'extra_usage': {'is_enabled': True, 'used_credits': 500, 'monthly_limit': 2000},
    }

    def test_no_session_hides_only_session(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(self.FULL_USAGE, None)):
            output = run_main(argv=['claude-vibeline', '--no-session'])
        assert 'sess' not in output
        assert 'week' in output
        assert 'opus' in output

    def test_no_weekly_hides_only_weekly(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(self.FULL_USAGE, None)):
            output = run_main(argv=['claude-vibeline', '--no-weekly'])
        assert 'sess' in output
        assert '20%' not in output
        assert 'opus' in output

    def test_no_opus_hides_only_opus(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(self.FULL_USAGE, None)):
            output = run_main(argv=['claude-vibeline', '--no-opus'])
        assert 'sess' in output
        assert '30%' not in output
        assert 'sonnet' in output

    def test_no_sonnet_hides_only_sonnet(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(self.FULL_USAGE, None)):
            output = run_main(argv=['claude-vibeline', '--no-sonnet'])
        assert 'opus' in output
        assert '40%' not in output
        assert 'extra' in output

    def test_no_extra_hides_only_extra(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(self.FULL_USAGE, None)):
            output = run_main(argv=['claude-vibeline', '--no-extra'])
        assert 'sess' in output
        assert 'extra' not in output


class TestUsageParts:
    def test_empty_usage_data(self) -> None:
        args = Args()
        assert usage_parts(args, {}) == []

    def test_none_usage(self) -> None:
        args = Args()
        assert usage_parts(args, None) == []

    def test_all_buckets_disabled(self) -> None:
        args = Args(session=False, weekly=False, opus=False, sonnet=False, extra=False)
        usage: UsageData = {
            'five_hour': {'utilization': 50},
            'seven_day': {'utilization': 50},
            'extra_usage': {'is_enabled': True, 'used_credits': 100},
        }
        assert usage_parts(args, usage) == []


class TestModelSection:
    def test_standard_model(self) -> None:
        result = model_section('Opus 4.6', 'high')
        assert 'Opus 4.6' in result
        assert '(high)' in result

    def test_low_effort(self) -> None:
        result = model_section('Sonnet 4.6', 'low')
        assert '(low)' in result

    def test_medium_effort(self) -> None:
        result = model_section('Opus 4.6', 'medium')
        assert '(medium)' in result

    def test_max_effort(self) -> None:
        result = model_section('Opus 4.6', 'max')
        assert '(max)' in result

    def test_haiku_skips_effort(self) -> None:
        result = model_section('Haiku 4.5', 'high')
        assert 'Haiku' in result
        assert '(' not in result

    def test_fallback_effort_shows_question_mark(self) -> None:
        result = model_section('Opus 4.6', 'high?')
        assert 'Opus 4.6' in result
        assert '(high?)' in result

    def test_unsupported_fallback_effort_defaults_to_medium(self) -> None:
        result = model_section('Sonnet 4.6', 'max?')
        assert '(medium?)' in result

    def test_unknown_model_skips_effort(self) -> None:
        result = model_section('CustomModel 1.0', 'high')
        assert 'CustomModel' in result
        assert '(' not in result


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


class TestBarRounding:
    def test_1_percent_width_8_rounds_to_zero(self) -> None:
        result = bar(1, 8)
        assert result.count(FILL) == 0
        assert result.count(EMPTY) == 8

    def test_7_percent_width_8_rounds_to_one(self) -> None:
        result = bar(7, 8)
        assert result.count(FILL) == 1
        assert result.count(EMPTY) == 7

    def test_99_percent_width_8(self) -> None:
        result = bar(99, 8)
        assert result.count(FILL) == 8
