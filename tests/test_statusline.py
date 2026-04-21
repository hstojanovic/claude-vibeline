import io
import json
import runpy
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from unittest import mock

from freezegun import freeze_time

from claude_vibeline.constants import ANSI_RE, EMPTY, FILL
from claude_vibeline.statusline import is_new_session, main

if TYPE_CHECKING:
    from claude_vibeline.schema import StdinData, UsageData


def _user(ts: str) -> str:
    return json.dumps({'type': 'user', 'timestamp': ts, 'message': {'content': 'hello'}})


def _assistant(ts: str) -> str:
    return json.dumps({'type': 'assistant', 'timestamp': ts})


STDIN_DATA = {
    'workspace': {'project_dir': '/home/user/my-project'},
    'model': {'display_name': 'Opus 4.6'},
    'context_window': {'used_percentage': 42.3},
}


def run_main(
    stdin_data: StdinData | dict[str, Any] | None = None,
    argv: list[str] | None = None,
    tmp_path: Path | None = None,
    settings_effort: str = 'medium',
) -> str:
    """
    Run main() with real effort resolution (no mocking resolve_effort).

    Mocks session_cache_dir and read_settings_effort to avoid touching real
    user files, but exercises the full resolve_effort -> model_section pipeline.
    """
    import claude_vibeline.statusline as _mod  # noqa: PLC0415

    data = stdin_data or STDIN_DATA
    argv = argv or ['claude-vibeline']
    cache_dir = (tmp_path or Path(tempfile.mkdtemp())) / 'sessions'
    cache_dir.mkdir(parents=True, exist_ok=True)
    stdin_buf = io.BytesIO(json.dumps(data).encode())
    stdout_buf = io.BytesIO()
    fake_stdin = io.TextIOWrapper(stdin_buf, encoding='utf-8')
    fake_stdout = io.TextIOWrapper(stdout_buf, encoding='utf-8')
    with (
        mock.patch('sys.argv', argv),
        mock.patch.object(_mod.sys, 'stdin', fake_stdin),
        mock.patch.object(_mod.sys, 'stdout', fake_stdout),
        mock.patch('claude_vibeline.effort.session_cache_dir', return_value=cache_dir),
        mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=cache_dir),
        mock.patch('claude_vibeline.effort.read_settings_effort', return_value=settings_effort),
    ):
        main()
        _mod.sys.stdout.flush()
        return stdout_buf.getvalue().decode('utf-8')


def _effort_transcript(effort: str, tmp_path: Path) -> tuple[str, str]:
    """
    Create a transcript with an effort command and return (transcript_path, session_id).
    """
    transcript = tmp_path / 'sess-main.jsonl'
    entry = json.dumps({
        'type': 'assistant',
        'timestamp': datetime.now(UTC).isoformat(),
        'message': {'content': f'Set effort level to {effort}'},
    })
    transcript.write_text(entry + '\n')
    return str(transcript), 'sess-main'


class TestMain:
    def test_full_pipeline_with_rate_limits(self, tmp_path: Path) -> None:
        transcript_path, session_id = _effort_transcript('high', tmp_path)
        data = {
            **STDIN_DATA,
            'transcript_path': transcript_path,
            'session_id': session_id,
            'rate_limits': {
                'five_hour': {'used_percentage': 19, 'resets_at': 4070908800},
                'seven_day': {'used_percentage': 3, 'resets_at': 4070908800},
            },
        }
        output = run_main(stdin_data=data, tmp_path=tmp_path)
        assert 'my-project' in output
        assert 'Opus' in output
        assert '(high)' in output
        assert '42%' in output
        assert '19%' in output
        assert '3%' in output
        assert '\u2265' not in output

    def test_no_rate_limits_in_stdin(self, tmp_path: Path) -> None:
        transcript_path, session_id = _effort_transcript('high', tmp_path)
        data = {**STDIN_DATA, 'transcript_path': transcript_path, 'session_id': session_id}
        output = run_main(stdin_data=data, tmp_path=tmp_path)
        assert 'my-project' in output
        assert 'Opus' in output
        assert '42%' in output
        assert 'sess' not in output

    def test_no_project_flag(self) -> None:
        output = run_main(argv=['claude-vibeline', '--no-project'])
        assert 'my-project' not in output
        assert 'Opus' in output

    def test_no_model_flag(self) -> None:
        output = run_main(argv=['claude-vibeline', '--no-model'])
        assert 'my-project' in output
        assert 'Opus' not in output

    def test_empty_model_name(self) -> None:
        data = {**STDIN_DATA, 'model': {'display_name': ''}}
        output = run_main(stdin_data=data)
        assert 'Unknown' in output

    def test_context_window_size_shown(self) -> None:
        data = {**STDIN_DATA, 'context_window': {'used_percentage': 42.3, 'context_window_size': 200_000}}
        output = run_main(stdin_data=data)
        assert '200k' in output
        assert '42%' in output

    def test_context_window_size_1m(self) -> None:
        data = {**STDIN_DATA, 'context_window': {'used_percentage': 10.0, 'context_window_size': 1_000_000}}
        output = run_main(stdin_data=data)
        assert '1M' in output

    def test_context_window_size_absent(self) -> None:
        output = run_main()
        assert '200k' not in output
        assert '1M' not in output
        assert '42%' in output

    def test_used_percentage_null_renders_as_zero(self) -> None:
        # Claude Code sends null early in a new session before the first API call
        data = {**STDIN_DATA, 'context_window': {'used_percentage': None, 'context_window_size': 200_000}}
        output = run_main(stdin_data=data)
        assert 'error' not in output
        assert '0%' in output
        assert '200k' in output

    def test_used_percentage_missing_renders_as_zero(self) -> None:
        data = {**STDIN_DATA, 'context_window': {'context_window_size': 200_000}}
        output = run_main(stdin_data=data)
        assert 'error' not in output
        assert '0%' in output

    def test_no_context_flag(self) -> None:
        output = run_main(argv=['claude-vibeline', '--no-context'])
        assert '42%' not in output

    def test_no_session_hides_stdin_rate_limit(self) -> None:
        data = {
            **STDIN_DATA,
            'rate_limits': {
                'five_hour': {'used_percentage': 10, 'resets_at': 4070908800},
                'seven_day': {'used_percentage': 5, 'resets_at': 4070908800},
            },
        }
        output = run_main(stdin_data=data, argv=['claude-vibeline', '--no-session'])
        assert 'sess' not in output
        assert 'week' in output

    def test_no_weekly_hides_stdin_rate_limit(self) -> None:
        data = {
            **STDIN_DATA,
            'rate_limits': {
                'five_hour': {'used_percentage': 10, 'resets_at': 4070908800},
                'seven_day': {'used_percentage': 5, 'resets_at': 4070908800},
            },
        }
        output = run_main(stdin_data=data, argv=['claude-vibeline', '--no-weekly'])
        assert 'sess' in output
        assert 'week' not in output

    def test_bar_width_flag(self) -> None:
        output = run_main(argv=['claude-vibeline', '--bar-width', '4'])
        assert output.count(FILL) + output.count(EMPTY) == 4

    def test_usage_flag_enables_api(self) -> None:
        usage_data: UsageData = {'extra_usage': {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(usage_data, None)):
            output = run_main(argv=['claude-vibeline', '--usage-api', '--extra'])
        assert 'extra' in output
        assert '2.50' in output

    def test_api_not_called_without_usage_flag(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage') as mock_fetch:
            run_main()
        mock_fetch.assert_not_called()

    def test_max_effort_from_transcript(self, tmp_path: Path) -> None:
        transcript_path, session_id = _effort_transcript('max', tmp_path)
        data = {**STDIN_DATA, 'transcript_path': transcript_path, 'session_id': session_id}
        output = run_main(stdin_data=data, tmp_path=tmp_path)
        assert '(max)' in output

    def test_max_transcript_on_sonnet_falls_back_to_settings(self, tmp_path: Path) -> None:
        transcript_path, session_id = _effort_transcript('max', tmp_path)
        data = {
            **STDIN_DATA,
            'transcript_path': transcript_path,
            'session_id': session_id,
            'model': {'display_name': 'Sonnet 4.6'},
        }
        output = run_main(stdin_data=data, tmp_path=tmp_path, settings_effort='high')
        assert '(high?)' in output
        assert '(max)' not in output
        assert '(medium?)' not in output

    def test_haiku_no_effort(self) -> None:
        data = {**STDIN_DATA, 'model': {'display_name': 'Haiku 4.5'}}
        output = run_main(stdin_data=data)
        assert 'Haiku' in output
        assert '(' not in output

    def test_stale_within_window(self) -> None:
        usage_data: UsageData = {'seven_day_opus': {'utilization': 19, 'resets_at': '2099-01-01T00:00:00+00:00'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(usage_data, time.time() - 120)):
            output = run_main(argv=['claude-vibeline', '--usage-api', '--opus'])
        assert '\u2265' in output
        assert '19%' in output
        assert '--' not in output

    @freeze_time('2026-03-07T12:00:00Z')
    def test_stale_past_reset(self) -> None:
        usage_data: UsageData = {'seven_day_opus': {'utilization': 19, 'resets_at': '2026-03-07T10:00:00+00:00'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(usage_data, time.time() - 120)):
            output = run_main(argv=['claude-vibeline', '--usage-api', '--opus'])
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
        output = run_main(stdin_data=data, tmp_path=tmp_path)
        assert 'cache' in output
        assert '\u25f7' in output

    def test_cache_expired_shown(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        transcript.write_text(_user(old_ts) + '\n')

        data = {**STDIN_DATA, 'transcript_path': str(transcript)}
        output = run_main(stdin_data=data, tmp_path=tmp_path)
        assert 'cache' in output
        assert '\u2717' in output

    def test_no_cache_flag(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        data = {**STDIN_DATA, 'transcript_path': str(transcript)}
        output = run_main(stdin_data=data, tmp_path=tmp_path, argv=['claude-vibeline', '--no-cache'])
        assert 'cache' not in output

    def test_debug_logs_to_file(self, tmp_path: Path) -> None:
        transcript_path, session_id = _effort_transcript('high', tmp_path)
        log_file = tmp_path / 'logs' / 'debug.log'
        data = {**STDIN_DATA, 'transcript_path': transcript_path, 'session_id': session_id}
        with mock.patch('claude_vibeline.debug.debug_log_path', return_value=log_file):
            run_main(stdin_data=data, tmp_path=tmp_path, argv=['claude-vibeline', '--debug'])
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert 'my-project' in entry['output']
        assert '\033[' not in entry['output']
        assert entry['effort'] == 'high'

    def test_settings_fallback_effort_in_fresh_session_has_no_question_mark(self) -> None:
        output = run_main()
        assert 'Opus 4.6' in output
        assert '(medium)' in output
        assert '(medium?)' not in output

    def test_settings_fallback_after_synthetic_resume_shows_question_mark(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'sess.jsonl'
        synthetic = json.dumps({
            'type': 'assistant',
            'timestamp': datetime.now(UTC).isoformat(),
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'No response requested.'}]},
        })
        transcript.write_text(synthetic + '\n')
        data = {**STDIN_DATA, 'transcript_path': str(transcript), 'session_id': 'sess-resumed'}
        output = run_main(stdin_data=data, tmp_path=tmp_path)
        assert '(medium?)' in output

    def test_effort_resolution_end_to_end(self, tmp_path: Path) -> None:
        """
        Full pipeline: transcript -> resolve_effort -> model_section -> session cache.

        Transcript has effort command -> resolve_effort reads it
        -> model_section displays it without '?' -> session cache persists it.
        """
        transcript_path, session_id = _effort_transcript('low', tmp_path)
        data = {**STDIN_DATA, 'transcript_path': transcript_path, 'session_id': session_id}
        output = run_main(stdin_data=data, tmp_path=tmp_path)
        assert '(low)' in output
        assert '?' not in ANSI_RE.sub('', output).split('(low)')[1].split(')')[0]
        cached = json.loads((tmp_path / 'sessions' / f'{session_id}.json').read_text())
        assert cached['effort'] == 'low'


class TestMessageLine:
    def test_unknown_flag_shows_error_message(self, tmp_path: Path) -> None:
        output = run_main(argv=['claude-vibeline', '--bogus'], tmp_path=tmp_path)
        # Statusline still renders despite the unknown flag
        assert 'Opus' in output
        # Plus an error message line below, prefixed with the program name
        assert 'claude-vibeline' in output
        assert 'bogus' in output.lower()
        assert '\n' in output

    def test_update_message_rendered_when_newer(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.check_for_update', return_value='99.0.0'):
            output = run_main(tmp_path=tmp_path)
        assert 'Opus' in output
        assert 'update' in output
        assert '99.0.0' in output

    def test_no_update_flag_suppresses_update_message(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.check_for_update', return_value='99.0.0') as check:
            output = run_main(argv=['claude-vibeline', '--no-update'], tmp_path=tmp_path)
        check.assert_not_called()
        assert 'update' not in output

    def test_error_beats_update(self, tmp_path: Path) -> None:
        # Even with update available, parse error takes priority
        with mock.patch('claude_vibeline.statusline.check_for_update', return_value='99.0.0'):
            output = run_main(argv=['claude-vibeline', '--bogus'], tmp_path=tmp_path)
        assert 'claude-vibeline' in output
        assert 'bogus' in output.lower()
        assert '99.0.0' not in output

    def test_update_check_failure_does_not_break_statusline(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.check_for_update', side_effect=RuntimeError('boom')):
            output = run_main(tmp_path=tmp_path)
        assert 'Opus' in output
        assert 'update' not in output
        assert 'claude-vibeline' not in output  # update failures are silent

    def test_render_failure_produces_error_message(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.render', side_effect=RuntimeError('boom')):
            output = run_main(tmp_path=tmp_path)
        assert 'claude-vibeline' in output
        assert 'RuntimeError' in output
        assert 'boom' in output

    def test_new_session_flag_passed_to_update_check(self, tmp_path: Path) -> None:
        data = {**STDIN_DATA, 'session_id': 'never-seen'}
        with mock.patch('claude_vibeline.statusline.check_for_update', return_value=None) as check:
            run_main(stdin_data=data, tmp_path=tmp_path)
        check.assert_called_once_with(is_new_session=True)

    def test_existing_session_is_not_new(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / 'sessions'
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / 'seen-before.json').write_text('{}')
        data = {**STDIN_DATA, 'session_id': 'seen-before'}
        with mock.patch('claude_vibeline.statusline.check_for_update', return_value=None) as check:
            run_main(stdin_data=data, tmp_path=tmp_path)
        check.assert_called_once_with(is_new_session=False)

    def test_is_new_session_none_id(self) -> None:
        assert is_new_session(None) is False

    def test_is_new_session_oserror_is_false(self) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', side_effect=OSError):
            assert is_new_session('some-session') is False


class TestProjectNameEdgeCases:
    def test_project_dir_dot(self) -> None:
        data = {**STDIN_DATA, 'workspace': {'project_dir': '.'}}
        output = run_main(stdin_data=data)
        assert '.' not in ANSI_RE.sub('', output).split()

    def test_project_dir_root(self) -> None:
        data = {**STDIN_DATA, 'workspace': {'project_dir': '/'}}
        output = run_main(stdin_data=data)
        assert 'Opus' in output

    def test_missing_workspace(self) -> None:
        data: StdinData = {'model': {'display_name': 'Opus 4.6'}, 'context_window': {'used_percentage': 10.0}}
        output = run_main(stdin_data=data)
        assert 'Opus' in output

    def test_empty_workspace(self) -> None:
        data = {**STDIN_DATA, 'workspace': {}}
        output = run_main(stdin_data=data)
        assert 'Opus' in output


class TestIndividualBucketFlags:
    STDIN_LIMITS: ClassVar[dict[str, Any]] = {
        'five_hour': {'used_percentage': 10, 'resets_at': 4070908800},
        'seven_day': {'used_percentage': 20, 'resets_at': 4070908800},
    }
    API_USAGE: ClassVar[UsageData] = {
        'seven_day_opus': {'utilization': 30, 'resets_at': '2099-01-01T00:00:00+00:00'},
        'seven_day_sonnet': {'utilization': 40, 'resets_at': '2099-01-01T00:00:00+00:00'},
        'extra_usage': {'is_enabled': True, 'used_credits': 500, 'monthly_limit': 2000},
    }

    def _run(self, *extra_argv: str) -> str:
        data = {**STDIN_DATA, 'rate_limits': self.STDIN_LIMITS}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(self.API_USAGE, None)):
            return run_main(stdin_data=data, argv=['claude-vibeline', *extra_argv])

    def test_no_session_hides_session(self) -> None:
        output = self._run('--no-session')
        assert 'sess' not in output
        assert 'week' in output

    def test_no_weekly_hides_weekly(self) -> None:
        output = self._run('--no-weekly')
        assert 'sess' in output
        assert 'week' not in output

    def test_opus_flag_shows_opus(self) -> None:
        output = self._run('--usage-api', '--opus')
        assert 'opus' in output
        assert '30%' in output

    def test_sonnet_flag_shows_sonnet(self) -> None:
        output = self._run('--usage-api', '--sonnet')
        assert 'sonnet' in output
        assert '40%' in output

    def test_extra_flag_shows_extra(self) -> None:
        output = self._run('--usage-api', '--extra')
        assert 'extra' in output
        assert '5.00' in output

    def test_usage_without_flags_shows_nothing_extra(self) -> None:
        output = self._run('--usage-api')
        assert 'opus' not in output
        assert 'sonnet' not in output
        assert 'extra' not in output

    def test_api_not_called_without_usage(self) -> None:
        data = {**STDIN_DATA, 'rate_limits': self.STDIN_LIMITS}
        with mock.patch('claude_vibeline.statusline.fetch_usage') as mock_fetch:
            run_main(stdin_data=data, argv=['claude-vibeline', '--opus', '--sonnet'])
        mock_fetch.assert_not_called()
