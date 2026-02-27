import io
import json
import runpy
import subprocess as sp
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from unittest import mock

import responses
from freezegun import freeze_time

from claude_vibeline.statusline import (
    ANSI_RE,
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
    bar,
    cache_path,
    debug_log_path,
    extra_section,
    fetch_usage,
    format_countdown,
    has_cache_gap,
    is_past,
    main,
    model_section,
    prompt_cache_section,
    read_effort,
    read_last_user_timestamp,
    read_last_user_timestamps,
    read_oauth_token,
    read_user_timestamps,
    token_from_entry,
    usage_parts,
    usage_section,
    visible_len,
    wrap_parts,
    write_cache,
    write_debug_log,
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
        # Should show countdown to March 1st
        assert '13d' in result

    @freeze_time('2026-03-15T12:00:00Z')
    def test_stale_same_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000}
        # Cached 2 minutes ago, same month
        stale_ts = time.time() - 120
        result = extra_section(extra, '$', stale_ts=stale_ts)
        assert result is not None
        assert '\u2265' in result
        assert '2.50' in result
        assert '?' not in result

    @freeze_time('2026-03-01T00:30:00Z')
    def test_stale_previous_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000}
        # Cached in February
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
        # No days component
        assert 'd' not in result

    @freeze_time('2026-02-24T10:00:00Z')
    def test_past_timestamp(self) -> None:
        result = format_countdown('2026-02-20T00:00:00+00:00')
        assert '0m' in result

    def test_invalid_iso_string(self) -> None:
        result = format_countdown('not-a-date')
        assert not result


class TestReadEffort:
    def test_valid_settings(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'effortLevel': 'low'}))
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            assert read_effort() == 'low'

    def test_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / 'nonexistent.json'
        with mock.patch.object(Path, 'expanduser', return_value=missing):
            assert read_effort() == 'default'

    def test_invalid_json(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        settings.write_text('{bad json')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            assert read_effort() == 'default'


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


class TestWriteCache:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        cache = tmp_path / 'cache' / 'usage.json'
        data: UsageData = {'five_hour': {'utilization': 10}}
        write_cache(cache, data)
        written = json.loads(cache.read_text())
        assert written['five_hour'] == {'utilization': 10}
        assert '_ts' in written

    def test_handles_oserror(self) -> None:
        cache = Path('/nonexistent/deeply/nested/usage.json')
        with mock.patch('claude_vibeline.statusline.Path.mkdir', side_effect=OSError):
            write_cache(cache, {'five_hour': {'utilization': 0}})


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
        cached = {'five_hour': {'utilization': 10}, '_ts': time.time()}
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
        old_data = {'five_hour': {'utilization': 10}, '_ts': time.time() - CACHE_TTL_SECONDS - 1}
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
        old_data = {'five_hour': {'utilization': 30}, '_ts': time.time() - CACHE_TTL_SECONDS - 1}
        cache.write_text(json.dumps(old_data))

        with (
            mock.patch('claude_vibeline.statusline.cache_path', return_value=cache),
            mock.patch('claude_vibeline.statusline.read_oauth_token', return_value=None),
        ):
            result, stale_ts = fetch_usage()

        assert result is not None
        assert result['five_hour']['utilization'] == 30
        assert isinstance(stale_ts, float)


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
    stdin_data: StdinData | dict[str, Any] | None = None, argv: list[str] | None = None, effort: str = 'high'
) -> str:
    import claude_vibeline.statusline as _mod  # noqa: PLC0415

    data = stdin_data or STDIN_DATA
    argv = argv or ['claude-vibeline']
    stdin_buf = io.BytesIO(json.dumps(data).encode())
    stdout_buf = io.BytesIO()
    # main() wraps sys.stdout.buffer with a new TextIOWrapper,
    # so provide objects with .buffer pointing to raw BytesIO.
    fake_stdin = io.TextIOWrapper(stdin_buf, encoding='utf-8')
    fake_stdout = io.TextIOWrapper(stdout_buf, encoding='utf-8')
    with (
        mock.patch('sys.argv', argv),
        mock.patch.object(_mod.sys, 'stdin', fake_stdin),
        mock.patch.object(_mod.sys, 'stdout', fake_stdout),
        mock.patch('claude_vibeline.statusline.read_effort', return_value=effort),
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

    def test_default_effort_becomes_high(self) -> None:
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(effort='default')
        assert '(high)' in output

    def test_haiku_no_effort(self) -> None:
        data = {**STDIN_DATA, 'model': {'display_name': 'Haiku 4.5'}}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'Haiku' in output
        # Haiku should not show effort level
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
        assert '\u25cf' in output

    def test_cache_expired_shown(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        transcript.write_text(_user(old_ts) + '\n')

        data = {**STDIN_DATA, 'transcript_path': str(transcript)}
        with mock.patch('claude_vibeline.statusline.fetch_usage', return_value=(None, None)):
            output = run_main(stdin_data=data)
        assert 'cache' in output
        assert '\u25cb' in output

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

    def test_effort_from_settings(self, tmp_path: Path) -> None:
        log_file = tmp_path / 'debug.log'
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', return_value=log_file):
            write_debug_log('test output', args, effort='low')
        entry = json.loads(log_file.read_text(encoding='utf-8').strip())
        assert entry['effort_from_settings'] == 'low'

    def test_oserror_silenced(self) -> None:
        args = Args(debug=True)
        with mock.patch('claude_vibeline.statusline.debug_log_path', side_effect=OSError):
            write_debug_log('test output', args)  # should not raise


class TestReadLastUserTimestamp:
    def test_valid_transcript(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = [_assistant('2026-03-07T10:00:00Z'), _user('2026-03-07T10:01:00Z'), _assistant('2026-03-07T10:01:30Z')]
        transcript.write_text('\n'.join(lines) + '\n')

        result = read_last_user_timestamp(str(transcript))
        assert result is not None
        expected = datetime.fromisoformat('2026-03-07T10:01:00Z').timestamp()
        assert abs(result - expected) < 1

    def test_no_user_messages(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_assistant('2026-03-07T10:00:00Z') + '\n')
        assert read_last_user_timestamp(str(transcript)) is None

    def test_missing_file(self) -> None:
        assert read_last_user_timestamp('/nonexistent/path.jsonl') is None

    def test_empty_file(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text('')
        assert read_last_user_timestamp(str(transcript)) is None

    def test_corrupt_jsonl(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text('{bad json\n')
        assert read_last_user_timestamp(str(transcript)) is None

    def test_missing_timestamp_field(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(json.dumps({'type': 'user'}) + '\n')
        assert read_last_user_timestamp(str(transcript)) is None


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

    def test_read_last_user_timestamps_limits(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        lines = [_user('2026-03-07T10:00:00Z'), _user('2026-03-07T10:01:00Z'), _user('2026-03-07T10:02:00Z')]
        transcript.write_text('\n'.join(lines) + '\n')

        assert len(read_last_user_timestamps(str(transcript))) == 2
        assert len(read_last_user_timestamps(str(transcript), count=1)) == 1

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
        """Tool results have type 'user' in transcripts and should be included."""
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
        """Entries without message field are not identified as user."""
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
        # user at idx 1, gap between idx 0 and idx 1
        assert has_cache_gap([now, now - PROMPT_CACHE_TTL - 1], last_user_idx=1)

    def test_gap_before_user_ignored(self) -> None:
        """Gap before last user message should not trigger ↻."""
        now = time.time()
        # tool_result, tool_result (gap here), user
        timestamps = [now, now - 30, now - PROMPT_CACHE_TTL - 60]
        assert not has_cache_gap(timestamps, last_user_idx=0)

    def test_gap_after_user_in_middle(self) -> None:
        """Gap between tool results after last user message."""
        now = time.time()
        # tool_result, tool_result (gap), user, old_tool
        timestamps = [now, now - PROMPT_CACHE_TTL - 10, now - PROMPT_CACHE_TTL - 20, now - PROMPT_CACHE_TTL - 30]
        assert has_cache_gap(timestamps, last_user_idx=2)

    def test_none_idx_checks_all(self) -> None:
        """When last_user_idx is None, check all pairs."""
        now = time.time()
        assert has_cache_gap([now, now - PROMPT_CACHE_TTL - 1], last_user_idx=None)

    def test_single_entry(self) -> None:
        assert not has_cache_gap([time.time()], last_user_idx=0)

    def test_empty(self) -> None:
        assert not has_cache_gap([])


class TestPromptCacheSection:
    def test_no_transcript(self) -> None:
        assert prompt_cache_section(None) is None

    def test_warm_cache(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert 'cache' in result
        assert '\u25cf' in result
        assert ':' in result

    def test_expired_cache(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        old_ts = (datetime.now(UTC) - timedelta(seconds=PROMPT_CACHE_TTL + 10)).isoformat()
        transcript.write_text(_user(old_ts) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u25cb' in result
        assert ':' in result

    def test_recached_after_gap(self, tmp_path: Path) -> None:
        """User message, then gap, then tool result — shows ↻."""
        transcript = tmp_path / 'session.jsonl'
        now = datetime.now(UTC)
        user_ts = (now - timedelta(seconds=PROMPT_CACHE_TTL + 60)).isoformat()
        recent_ts = now.isoformat()
        lines = [_user(user_ts), _assistant(user_ts), _tool_result(recent_ts)]
        transcript.write_text('\n'.join(lines) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u21bb' in result
        assert '\u25cb' not in result
        assert ':' in result

    def test_no_recache_indicator_when_no_gap(self, tmp_path: Path) -> None:
        """No ↻ when entries after last user are within cache TTL."""
        transcript = tmp_path / 'session.jsonl'
        now = datetime.now(UTC)
        user_ts = (now - timedelta(seconds=60)).isoformat()
        recent_ts = now.isoformat()
        lines = [_user(user_ts), _assistant(user_ts), _tool_result(recent_ts)]
        transcript.write_text('\n'.join(lines) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u25cf' in result
        assert '\u21bb' not in result
        assert '\u25cb' not in result

    def test_no_recache_indicator_single_message(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        ts = datetime.now(UTC).isoformat()
        transcript.write_text(_user(ts) + '\n')

        result = prompt_cache_section(str(transcript))
        assert result is not None
        assert '\u21bb' not in result

    def test_gap_before_user_ignored(self, tmp_path: Path) -> None:
        """Gap before last user message does NOT show ↻."""
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
        assert '\u25cf' in result
        assert '\u21bb' not in result

    def test_gap_after_user_shows_recached(self, tmp_path: Path) -> None:
        """Gap between tool results after last user shows ↻."""
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

    def test_no_user_messages(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        transcript.write_text(_assistant('2026-03-07T10:00:00Z') + '\n')
        assert prompt_cache_section(str(transcript)) is None


class TestChunkedTranscriptReading:
    """Verify read_user_timestamps works when transcript exceeds TAIL_CHUNK."""

    def test_user_found_in_second_chunk(self, tmp_path: Path) -> None:
        transcript = tmp_path / 'session.jsonl'
        # Build a transcript larger than TAIL_CHUNK where the user message
        # is near the beginning (only reachable via multi-chunk reading).
        user_line = _user('2026-03-07T10:00:00Z')
        filler_line = _assistant('2026-03-07T10:00:30Z')
        # Each JSONL line is ~80 bytes; need enough to exceed TAIL_CHUNK.
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
    """Verify wrap_parts uses visible_len, not len, for width calculation."""

    def test_ansi_parts_fit_on_one_line(self) -> None:
        # Each part is ~5 visible chars but ~20+ raw chars with ANSI codes.
        parts = [f'{ORANGE}hello{RESET}', f'{PERC}world{RESET}']
        result = wrap_parts(parts, 40)
        assert '\n' not in result

    def test_ansi_parts_wrap_at_visible_width(self) -> None:
        # Two parts, each ~10 visible chars. Should wrap at columns=15.
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
        # Path('/').name == '', so no project shown
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
        data: StdinData = {'model': {'display_name': 'Opus 4.6'}}
        with mock.patch('claude_vibeline.statusline.read_effort', return_value='high'):
            result = model_section(data)
        assert 'Opus 4.6' in result
        assert '(high)' in result

    def test_low_effort(self) -> None:
        data: StdinData = {'model': {'display_name': 'Sonnet 4.6'}}
        with mock.patch('claude_vibeline.statusline.read_effort', return_value='low'):
            result = model_section(data)
        assert '(low)' in result

    def test_haiku_skips_effort(self) -> None:
        data: StdinData = {'model': {'display_name': 'Haiku 4.5'}}
        result = model_section(data)
        assert 'Haiku' in result
        assert '(' not in result

    def test_missing_model(self) -> None:
        data: StdinData = {}
        with mock.patch('claude_vibeline.statusline.read_effort', return_value='high'):
            result = model_section(data)
        assert 'Unknown' in result


class TestIsUserMessage:
    """Edge cases for is_user_message via read_user_timestamps."""

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
        # round(1 * 8 / 100) = round(0.08) = 0
        assert result.count(FILL) == 0
        assert result.count(EMPTY) == 8

    def test_7_percent_width_8_rounds_to_one(self) -> None:
        result = bar(7, 8)
        # round(7 * 8 / 100) = round(0.56) = 1
        assert result.count(FILL) == 1
        assert result.count(EMPTY) == 7

    def test_99_percent_width_8(self) -> None:
        result = bar(99, 8)
        # round(99 * 8 / 100) = round(7.92) = 8
        assert result.count(FILL) == 8
