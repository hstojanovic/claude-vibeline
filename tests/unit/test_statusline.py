"""Unit tests for `statusline.py` helpers: `is_new_session` cache lookup including None/OSError edge cases."""

from pathlib import Path
from unittest import mock

from claude_vibeline.statusline import is_new_session


class TestIsNewSession:
    def test_none_id(self) -> None:
        assert is_new_session(None) is False

    def test_oserror_is_false(self) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', side_effect=OSError):
            assert is_new_session('some-session') is False

    def test_unseen_session_is_new(self, tmp_path: Path) -> None:
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            assert is_new_session('never-seen') is True

    def test_cached_session_is_not_new(self, tmp_path: Path) -> None:
        (tmp_path / 'seen-before.json').write_text('{}')
        with mock.patch('claude_vibeline.statusline.session_cache_dir', return_value=tmp_path):
            assert is_new_session('seen-before') is False
