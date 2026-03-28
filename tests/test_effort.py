import json
import os
from pathlib import Path
from unittest import mock

import pytest

from claude_vibeline import __version__ as app_version
from claude_vibeline.effort import (
    EffortScanner,
    cleanup_session_cache,
    parse_effort_from_line,
    read_session_cache,
    read_settings_effort,
    resolve_effort,
    scan_transcript_effort,
    session_cache_dir,
    write_session_cache,
)


def transcript_line(content: str) -> str:
    return json.dumps({'type': 'user', 'message': {'content': content}})


class TestParseEffortFromLine:
    @pytest.mark.parametrize(
        ('line', 'expected'),
        [
            ('Set model to Sonnet 4.6 with low effort', 'low'),
            ('Set model to Sonnet 4.6 with medium effort', 'medium'),
            ('Set model to Opus 4.6 (1M context) (default) with high effort', 'high'),
            ('Set model to Opus 4.6 with max effort', 'max'),
            ('Set model to Sonnet 4.6', None),
            ('Set effort level to low', 'low'),
            ('Set effort level to medium', 'medium'),
            ('Set effort level to high', 'high'),
            ('Set effort level to max', 'max'),
            ('Effort level set to auto', 'auto'),
            ('hello world', None),
            ('', None),
            ('Set effort level to EXTREME', None),
            ('Set model to Opus 4.6 with  high effort', None),
        ],
    )
    def test_parsing(self, line: str, expected: str | None) -> None:
        assert parse_effort_from_line(line) == expected


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
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path / 'cache'):
            result = resolve_effort(str(transcript), 'sess-1')
        assert result == 'max'

    def test_cached_effort_used_when_no_transcript(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (cache_dir / 'sess-1.json').write_text(json.dumps({'effort': 'high', '_v': app_version}))
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir):
            result = resolve_effort(None, 'sess-1')
        assert result == 'high'

    def test_no_effort_no_cache_falls_back_to_settings(self, tmp_path: Path) -> None:
        with (
            mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path),
            mock.patch('claude_vibeline.effort.read_settings_effort', return_value='medium?'),
        ):
            result = resolve_effort(None, 'sess-new')
        assert result == 'medium?'

    def test_no_session_id_falls_back_to_settings(self) -> None:
        with mock.patch('claude_vibeline.effort.read_settings_effort', return_value='medium?'):
            result = resolve_effort(None, None)
        assert result == 'medium?'

    def test_scan_beats_cached_effort(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        (cache_dir / 'sess-1.json').write_text(json.dumps({'effort': 'high'}))
        transcript = tmp_path / 'session.jsonl'
        content = '<local-command-stdout>Set effort level to max</local-command-stdout>'
        transcript.write_text(transcript_line(content) + '\n')
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir):
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
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir):
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
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir):
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
            mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir),
            mock.patch('claude_vibeline.effort.read_settings_effort', return_value='medium?'),
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
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path):
            assert read_session_cache('missing') == {}

    def test_write_and_read(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path):
            write_session_cache('sess-1', {'effort': 'high'})
            assert read_session_cache('sess-1')['effort'] == 'high'

    def test_write_overwrites(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path):
            write_session_cache('sess-1', {'effort': 'high'})
            write_session_cache('sess-1', {'effort': 'low'})
            assert read_session_cache('sess-1')['effort'] == 'low'

    def test_version_mismatch_ignored(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path):
            (tmp_path / 'sess-1.json').write_text(json.dumps({'effort': 'high', '_v': '0.0.0'}))
            assert read_session_cache('sess-1') == {}

    def test_write_oserror_silenced(self, tmp_path: Path) -> None:
        with (
            mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path),
            mock.patch.object(Path, 'mkdir', side_effect=OSError),
        ):
            write_session_cache('sess-fail', {'effort': 'high'})

    def test_returns_path(self) -> None:
        result = session_cache_dir()
        assert result.name == 'sessions'
        assert 'claude-vibeline' in str(result)

    def test_read_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / 'bad.json').write_text('{bad json')
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path):
            assert read_session_cache('bad') == {}

    def test_write_merges_fields(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.effort.session_cache_dir', return_value=tmp_path):
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
