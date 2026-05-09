"""
Unit tests for `args.py`.

Covers cappa CLI parsing: defaults, flags, unknown/invalid/missing arguments,
help/version exits, and stderr suppression.
"""

from unittest import mock

import cappa
import pytest

from claude_vibeline.args import Args, parse_args


class TestParseArgs:
    def test_defaults(self) -> None:
        with mock.patch('sys.argv', ['claude-vibeline']):
            args, err = parse_args()
        assert isinstance(args, Args)
        assert err is None
        assert args.columns == 80
        assert args.update is True

    def test_known_flag(self) -> None:
        with mock.patch('sys.argv', ['claude-vibeline', '--no-update']):
            args, err = parse_args()
        assert err is None
        assert args.update is False

    def test_unknown_flag_returns_error(self) -> None:
        with mock.patch('sys.argv', ['claude-vibeline', '--bogus']):
            args, err = parse_args()
        assert isinstance(args, Args)
        assert args.columns == 80  # defaults
        assert err is not None
        assert 'bogus' in err.lower()

    def test_invalid_value_returns_error(self) -> None:
        with mock.patch('sys.argv', ['claude-vibeline', '--columns', 'abc']):
            _, err = parse_args()
        assert err is not None
        assert 'columns' in err.lower()

    def test_missing_value_returns_error(self) -> None:
        with mock.patch('sys.argv', ['claude-vibeline', '--columns']):
            _, err = parse_args()
        assert err is not None

    def test_help_still_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch('sys.argv', ['claude-vibeline', '-h']), pytest.raises(cappa.HelpExit):
            parse_args()
        captured = capsys.readouterr()
        assert 'Usage:' in captured.out or 'usage:' in captured.out.lower()

    def test_version_still_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch('sys.argv', ['claude-vibeline', '--version']), pytest.raises(cappa.Exit) as exc:
            parse_args()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip()  # version is printed

    def test_error_not_leaked_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch('sys.argv', ['claude-vibeline', '--bogus']):
            parse_args()
        captured = capsys.readouterr()
        # cappa's formatted error should be suppressed
        assert 'Unrecognized' not in captured.err
