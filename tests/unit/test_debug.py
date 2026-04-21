import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from claude_vibeline.args import Args
from claude_vibeline.constants import DEBUG_LOG_MAX_BYTES, NBSP
from claude_vibeline.debug import cleanup_stale_tmp, debug_log_path, write_debug_log

if TYPE_CHECKING:
    from claude_vibeline.schema import StdinData, UsageData


class TestDebugLogPath:
    def test_returns_expected_path(self) -> None:
        path = debug_log_path()
        assert isinstance(path, Path)
        assert path.name == 'debug.log'
        assert 'claude-vibeline' in str(path)


class TestWriteDebugLog:
    def test_appends_to_existing(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        log_file.write_text('{"old": true}\n')
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        lines = log_file.read_text(encoding='utf-8').strip().splitlines()
        assert len(lines) == 2
        entry = json.loads(lines[1])
        assert entry['output'] == 'test output'

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'nested' / 'dir' / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        assert log_file.exists()

    def test_truncates_large_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        log_file.write_bytes(b'x' * (DEBUG_LOG_MAX_BYTES + 1))
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
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
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        content = log_file.read_text(encoding='utf-8')
        for line in content.strip().splitlines():
            json.loads(line)

    def test_jsonl_format_with_args(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True, bar_width=12)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['args']['bar_width'] == 12
        assert 'ts' in entry
        assert entry['output'] == 'test output'

    def test_session_id_from_transcript(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        stdin_data: StdinData = {'transcript_path': '/home/user/.claude/sessions/abc-123-def.jsonl'}
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args, stdin_data=stdin_data)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['session'] == 'abc-123-def'

    def test_no_session_without_transcript(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['session'] is None

    def test_effort_in_entry(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args, effort='high')
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['effort'] == 'high'

    def test_oserror_silenced(self) -> None:
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', side_effect=OSError):
            write_debug_log('test output', args)

    def test_usage_data_in_entry(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        usage: UsageData = {'five_hour': {'utilization': 42}}
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args, usage_data=usage, stale_ts=123.0)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['usage']['five_hour']['utilization'] == 42
        assert entry['stale_ts'] == 123

    def test_nbsp_replaced_in_output(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log(f'a{NBSP}b', args)
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['output'] == 'a b'

    def test_truncation_cleans_up_stale_tmp(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        stale1 = tmp_path / 'tmp9hc05iwg'
        stale2 = tmp_path / 'tmplx52v8ho'
        stale1.write_text('old')
        stale2.write_text('old')
        lines: list[str] = []
        while sum(len(ln) for ln in lines) < DEBUG_LOG_MAX_BYTES + 1000:
            lines.append(json.dumps({'i': len(lines), 'pad': 'x' * 200}) + '\n')
        log_file.write_text(''.join(lines))
        args = Args(debug=True)
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            write_debug_log('test output', args)
        assert not stale1.exists()
        assert not stale2.exists()
        assert log_file.exists()

    def test_truncation_cleans_up_on_write_error(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        lines: list[str] = []
        while sum(len(ln) for ln in lines) < DEBUG_LOG_MAX_BYTES + 1000:
            lines.append(json.dumps({'i': len(lines), 'pad': 'x' * 200}) + '\n')
        log_file.write_text(''.join(lines))
        args = Args(debug=True)
        with (
            mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file),
            mock.patch('os.write', side_effect=OSError('disk full')),
        ):
            write_debug_log('test output', args)
        assert len(list(tmp_path.glob('*.tmp'))) == 0

    def test_truncation_replace_failure_cleans_up_tmp(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        lines: list[str] = []
        while sum(len(ln) for ln in lines) < DEBUG_LOG_MAX_BYTES + 1000:
            lines.append(json.dumps({'i': len(lines), 'pad': 'x' * 200}) + '\n')
        log_file.write_text(''.join(lines))
        args = Args(debug=True)
        with (
            mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file),
            mock.patch('pathlib.Path.replace', side_effect=OSError('locked')),
        ):
            write_debug_log('test output', args)
        assert len(list(tmp_path.glob('tmp*'))) == 0


class TestCleanupStaleTmp:
    def test_removes_tmp_files(self, tmp_path: Path) -> None:
        (tmp_path / 'tmp12345').write_text('stale')
        (tmp_path / 'tmpabcdef').write_text('stale')
        (tmp_path / 'debug.log').write_text('keep')
        cleanup_stale_tmp(tmp_path)
        assert not (tmp_path / 'tmp12345').exists()
        assert not (tmp_path / 'tmpabcdef').exists()
        assert (tmp_path / 'debug.log').exists()

    def test_ignores_nonexistent_dir(self) -> None:
        cleanup_stale_tmp(Path('/nonexistent/dir'))
