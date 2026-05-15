"""
Unit tests for `display.py`.

Covers bar rendering (clamp/round), per-segment formatters (cache, usage, model,
extra, stdin), countdown/context-size formatting, ANSI-aware wrapping, and
visible-length math.
"""

import time
from typing import TYPE_CHECKING

import pytest
import time_machine

from claude_vibeline.args import Args
from claude_vibeline.constants import ANSI_RE, CACHE_LOW_THRESHOLD, EMPTY, FILL, MODEL, NBSP, PERC, RESET, SEP
from claude_vibeline.display import (
    api_usage_parts,
    bar,
    cache_section,
    extra_section,
    format_cache_countdown,
    format_context_size,
    format_countdown,
    is_past,
    model_section,
    stdin_section,
    stdin_usage_parts,
    usage_section,
    visible_len,
    wrap_message,
    wrap_parts,
)

if TYPE_CHECKING:
    from claude_vibeline.schema import ExtraUsage, StdinRateLimits, UsageBucket, UsageData


class TestBar:
    @pytest.mark.parametrize(
        ('perc', 'width', 'filled', 'empty'),
        [(0, 8, 0, 8), (50, 8, 4, 4), (100, 8, 8, 0), (25, 4, 1, 3), (1, 8, 0, 8), (99, 8, 8, 0)],
    )
    def test_fill_ratio(self, perc: int, width: int, filled: int, empty: int) -> None:
        result = bar(perc, width)
        assert result.count(FILL) == filled
        assert result.count(EMPTY) == empty

    def test_width_zero(self) -> None:
        assert not bar(50, 0)

    def test_negative_pct_clamped(self) -> None:
        assert bar(-10, 8).count(EMPTY) == 8

    def test_pct_over_100_clamped(self) -> None:
        assert bar(200, 8).count(FILL) == 8

    def test_negative_width_clamped(self) -> None:
        assert not bar(50, -5)


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


class TestFormatCacheCountdown:
    @pytest.mark.parametrize(
        ('secs', 'expected'),
        [(300, '5m'), (240, '4m'), (61, '1m'), (60, '1m'), (59, '59s'), (47, '47s'), (1, '1s'), (0, '0s')],
    )
    def test_formatting(self, secs: int, expected: str) -> None:
        assert format_cache_countdown(secs) == expected


class TestCacheSection:
    def test_warm(self) -> None:
        result = cache_section(250, gap=False)
        assert '\u25f7' in result
        assert 'cache' in result

    def test_low(self) -> None:
        result = cache_section(CACHE_LOW_THRESHOLD, gap=False)
        assert '\u26a0' in result

    def test_expired(self) -> None:
        result = cache_section(0, gap=False)
        assert '\u2717' in result
        assert '0s' in result

    def test_expired_negative(self) -> None:
        result = cache_section(-60, gap=False)
        assert '\u2717' in result
        assert '0s' in result

    def test_gap_shown(self) -> None:
        result = cache_section(250, gap=True)
        assert '\u21bb' in result
        assert '\u25f7' in result

    def test_gap_on_expired(self) -> None:
        result = cache_section(0, gap=True)
        assert '\u21bb' in result
        assert '\u2717' in result


class TestIsPast:
    @time_machine.travel('2026-03-07T12:00:00Z', tick=False)
    def test_future(self) -> None:
        assert not is_past('2026-03-07T15:00:00+00:00')

    @time_machine.travel('2026-03-07T12:00:00Z', tick=False)
    def test_past(self) -> None:
        assert is_past('2026-03-07T10:00:00+00:00')

    def test_invalid(self) -> None:
        assert not is_past('not-a-date')


class TestUsageSection:
    def test_valid_data(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2099-01-01T00:00:00+00:00'}
        result = usage_section('sess', usage, 8)
        assert 'sess' in result
        assert '42%' in result
        assert '\u2265' not in result

    def test_none_bucket_is_pending(self) -> None:
        result = usage_section('sess', None, 8)
        assert 'sess' in result
        assert '\u2014' in result
        assert FILL not in result

    def test_none_pct_is_pending(self) -> None:
        usage: UsageBucket = {'utilization': None, 'resets_at': '2099-01-01T00:00:00+00:00'}
        result = usage_section('sess', usage, 8)
        assert 'sess' in result
        assert '\u2014' in result
        assert FILL not in result

    def test_without_resets_at(self) -> None:
        usage: UsageBucket = {'utilization': 25}
        result = usage_section('week', usage, 8)
        assert '25%' in result

    def test_stale_within_window(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2099-01-01T00:00:00+00:00'}
        result = usage_section('sess', usage, 8, stale_ts=time.time() - 120)
        assert '\u2265' in result
        assert '42%' in result
        assert '\u21bb' not in result

    @time_machine.travel('2026-03-07T12:00:00Z', tick=False)
    def test_stale_past_reset(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2026-03-07T10:00:00+00:00'}
        result = usage_section('sess', usage, 8, stale_ts=time.time() - 120)
        assert '\u21bb' in result
        assert '42%' not in result
        assert '\u2265' not in result
        assert FILL not in result

    @time_machine.travel('2026-03-07T12:00:00Z', tick=False)
    def test_fresh_past_reset(self) -> None:
        usage: UsageBucket = {'utilization': 42, 'resets_at': '2026-03-07T10:00:00+00:00'}
        result = usage_section('sess', usage, 8)
        assert '\u21bb' in result
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

    def test_none_extra_is_pending(self) -> None:
        result = extra_section(None, '$')
        assert result is not None
        assert 'extra' in result
        assert '\u2014' in result

    def test_missing_used_credits_is_pending(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'monthly_limit': 2000}
        result = extra_section(extra, '$')
        assert result is not None
        assert 'extra' in result
        assert '\u2014' in result

    @time_machine.travel('2026-02-15T12:00:00Z', tick=False)
    def test_countdown_to_next_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 100, 'monthly_limit': 2000}
        result = extra_section(extra, '$')
        assert result is not None
        assert '13d' in result

    @time_machine.travel('2026-03-15T12:00:00Z', tick=False)
    def test_stale_same_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000}
        stale_ts = time.time() - 120
        result = extra_section(extra, '$', stale_ts=stale_ts)
        assert result is not None
        assert '\u2265' in result
        assert '2.50' in result
        assert '\u21bb' not in result

    @time_machine.travel('2026-03-01T00:30:00Z', tick=False)
    def test_stale_previous_month(self) -> None:
        extra: ExtraUsage = {'is_enabled': True, 'used_credits': 250, 'monthly_limit': 2000}
        stale_ts = time.time() - 3600
        result = extra_section(extra, '$', stale_ts=stale_ts)
        assert result is not None
        assert '\u21bb' in result
        assert '2.50' not in result


class TestFormatCountdown:
    @time_machine.travel('2026-02-24T10:00:00Z', tick=False)
    def test_future_days_and_hours(self) -> None:
        result = format_countdown('2026-02-27T14:30:00+00:00')
        assert '3d' in result
        assert '4h' in result

    @time_machine.travel('2026-02-24T10:00:00Z', tick=False)
    def test_hours_and_minutes(self) -> None:
        result = format_countdown('2026-02-24T13:45:00+00:00')
        assert '3h' in result
        assert '45m' in result
        assert 'd' not in result

    @time_machine.travel('2026-02-24T10:00:00Z', tick=False)
    def test_past_timestamp_clamps_to_zero(self) -> None:
        result = format_countdown('2026-02-20T00:00:00+00:00')
        assert '0m' in result

    def test_invalid_iso_string(self) -> None:
        result = format_countdown('not-a-date')
        assert not result


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


class TestWrapMessage:
    def test_short_fits_on_one_line(self) -> None:
        assert wrap_message('hello world', 40) == 'hello world'

    def test_wraps_at_word_boundary(self) -> None:
        result = wrap_message('aaa bbb ccc ddd', 8)
        assert result == 'aaa bbb\nccc ddd'

    def test_visible_length_ignores_ansi(self) -> None:
        msg = f'{MODEL}{"a" * 10}{RESET} {PERC}{"b" * 10}{RESET}'
        result = wrap_message(msg, 15)
        assert '\n' in result
        for line in result.split('\n'):
            assert ANSI_RE.sub('', line).strip()

    def test_word_longer_than_columns(self) -> None:
        # Overlong words are emitted on their own line rather than split
        result = wrap_message('aaa verylongword bbb', 5)
        lines = result.split('\n')
        assert 'verylongword' in lines

    def test_empty_string(self) -> None:
        assert not wrap_message('', 40)


class TestWrapPartsAnsi:
    def test_ansi_parts_fit_on_one_line(self) -> None:
        parts = [f'{MODEL}hello{RESET}', f'{PERC}world{RESET}']
        result = wrap_parts(parts, 40)
        assert '\n' not in result

    def test_ansi_parts_wrap_at_visible_width(self) -> None:
        p1 = f'{MODEL}{"a" * 10}{RESET}'
        p2 = f'{PERC}{"b" * 10}{RESET}'
        result = wrap_parts([p1, p2], 15)
        assert '\n' in result


class TestStdinSection:
    def test_renders_percentage_and_countdown(self) -> None:
        result = stdin_section('sess', {'used_percentage': 17.5, 'resets_at': 4070908800}, 8)
        assert '18%' in result
        assert 'sess' in result

    def test_without_resets_at(self) -> None:
        result = stdin_section('sess', {'used_percentage': 5.0}, 8)
        assert '5%' in result

    def test_past_resets_at_shows_recycle(self) -> None:
        result = stdin_section('sess', {'used_percentage': 10.0, 'resets_at': 0}, 8)
        assert '↻' in result
        assert '10%' not in result
        assert FILL not in result

    def test_short_countdown_shows_minutes(self) -> None:
        soon = int(time.time()) + 3600
        result = stdin_section('sess', {'used_percentage': 10.0, 'resets_at': soon}, 8)
        assert 'm' in result

    def test_none_bucket_is_pending(self) -> None:
        result = stdin_section('sess', None, 8)
        assert 'sess' in result
        assert '—' in result
        assert FILL not in result

    def test_missing_percentage_is_pending(self) -> None:
        result = stdin_section('sess', {}, 8)
        assert 'sess' in result
        assert '—' in result
        assert FILL not in result


class TestStdinUsageParts:
    def test_session_from_stdin(self) -> None:
        args = Args()
        limits: StdinRateLimits = {'five_hour': {'used_percentage': 17, 'resets_at': 4070908800}}
        result = stdin_usage_parts(args, limits)
        assert len(result) == 2
        assert 'sess' in result[0]
        assert '17%' in result[0]
        assert 'week' in result[1]
        assert '—' in result[1]

    def test_weekly_from_stdin(self) -> None:
        args = Args()
        limits: StdinRateLimits = {'seven_day': {'used_percentage': 7, 'resets_at': 4070908800}}
        result = stdin_usage_parts(args, limits)
        assert len(result) == 2
        assert 'sess' in result[0]
        assert '—' in result[0]
        assert 'week' in result[1]
        assert '7%' in result[1]

    def test_both_from_stdin(self) -> None:
        args = Args()
        limits: StdinRateLimits = {
            'five_hour': {'used_percentage': 15, 'resets_at': 4070908800},
            'seven_day': {'used_percentage': 7, 'resets_at': 4070908800},
        }
        result = stdin_usage_parts(args, limits)
        assert len(result) == 2

    def test_disabled_session(self) -> None:
        args = Args(session=False)
        limits: StdinRateLimits = {'five_hour': {'used_percentage': 15, 'resets_at': 4070908800}}
        result = stdin_usage_parts(args, limits)
        assert len(result) == 1
        assert 'sess' not in result[0]
        assert 'week' in result[0]
        assert '—' in result[0]

    def test_both_disabled_returns_empty(self) -> None:
        args = Args(session=False, weekly=False)
        assert stdin_usage_parts(args, None) == []

    def test_empty_limits_pending(self) -> None:
        args = Args()
        result = stdin_usage_parts(args, {})
        assert len(result) == 2
        assert all('—' in p for p in result)

    def test_none_limits_pending(self) -> None:
        args = Args()
        result = stdin_usage_parts(args, None)
        assert len(result) == 2
        assert any('sess' in p for p in result)
        assert any('week' in p for p in result)
        assert all('—' in p for p in result)


class TestApiUsageParts:
    def test_empty_usage_data_renders_pending(self) -> None:
        args = Args(opus=True)
        result = api_usage_parts(args, {})
        assert len(result) == 1
        assert 'opus' in result[0]
        assert '—' in result[0]

    def test_none_usage_data_renders_pending(self) -> None:
        args = Args(opus=True, sonnet=True, extra=True)
        result = api_usage_parts(args, None)
        assert len(result) == 3
        assert any('opus' in p for p in result)
        assert any('sonnet' in p for p in result)
        assert any('extra' in p for p in result)
        assert all('—' in p for p in result)

    def test_all_disabled_by_default(self) -> None:
        args = Args()
        usage: UsageData = {
            'seven_day_opus': {'utilization': 50},
            'extra_usage': {'is_enabled': True, 'used_credits': 100},
        }
        assert api_usage_parts(args, usage) == []

    def test_opus_bucket(self) -> None:
        args = Args(opus=True)
        usage: UsageData = {'seven_day_opus': {'utilization': 30}}
        result = api_usage_parts(args, usage)
        assert len(result) == 1
        assert 'opus' in result[0]

    def test_extra_disabled_account_stays_omitted(self) -> None:
        args = Args(extra=True)
        usage: UsageData = {'extra_usage': {'is_enabled': False, 'used_credits': 0}}
        assert api_usage_parts(args, usage) == []


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

    def test_opus_4_7_xhigh(self) -> None:
        result = model_section('Opus 4.7', 'xhigh')
        assert 'Opus 4.7' in result
        assert '(xhigh)' in result

    def test_opus_4_7_max(self) -> None:
        result = model_section('Opus 4.7', 'max')
        assert '(max)' in result

    def test_opus_4_6_xhigh_falls_back_to_high(self) -> None:
        result = model_section('Opus 4.6', 'xhigh?')
        assert '(high?)' in result

    def test_sonnet_4_6_xhigh_falls_back_to_high(self) -> None:
        result = model_section('Sonnet 4.6', 'xhigh?')
        assert '(high?)' in result

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

    def test_1m_context_suffix(self) -> None:
        result = model_section('Opus 4.6 (1M context)', 'high')
        assert 'Opus 4.6 (1M context)' in result
        assert '(high)' in result

    def test_legacy_opus_skips_effort(self) -> None:
        result = model_section('Opus 4.5', 'high')
        assert 'Opus 4.5' in result
        assert '(' not in result

    def test_legacy_sonnet_skips_effort(self) -> None:
        result = model_section('Sonnet 4.5', 'medium')
        assert 'Sonnet 4.5' in result
        assert '(' not in result

    def test_unknown_model_skips_effort(self) -> None:
        result = model_section('CustomModel 1.0', 'high')
        assert 'CustomModel' in result
        assert '(' not in result
