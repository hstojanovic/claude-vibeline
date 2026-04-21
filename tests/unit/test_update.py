import json
import time
from pathlib import Path
from unittest import mock

import responses

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import PYPI_URL, UPDATE_CHECK_INTERVAL
from claude_vibeline.update import (
    _parse_version,
    check_for_update,
    fetch_latest_version,
    format_update_message,
    is_newer,
    read_update_cache,
    update_cache_path,
    write_update_cache,
)


class TestUpdateCachePath:
    def test_returns_expected_path(self) -> None:
        path = update_cache_path()
        assert isinstance(path, Path)
        assert path.name == 'update.json'
        assert 'claude-vibeline' in str(path)


class TestReadWriteUpdateCache:
    def test_round_trip(self, tmp_path: Path) -> None:
        cache = tmp_path / 'update.json'
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            write_update_cache({'latest': '9.9.9', 'checked_ts': 1234.5})
            data = read_update_cache()
        assert data == {'latest': '9.9.9', 'checked_ts': 1234.5}

    def test_missing_file(self, tmp_path: Path) -> None:
        cache = tmp_path / 'missing.json'
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            assert read_update_cache() == {}

    def test_invalid_json(self, tmp_path: Path) -> None:
        cache = tmp_path / 'update.json'
        cache.write_text('{bad')
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            assert read_update_cache() == {}

    def test_version_mismatch_invalidates(self, tmp_path: Path) -> None:
        cache = tmp_path / 'update.json'
        cache.write_text(json.dumps({'latest': '9.9.9', '_v': 'old-version'}))
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            assert read_update_cache() == {}

    def test_write_handles_oserror(self, tmp_path: Path) -> None:
        cache = tmp_path / 'nested' / 'update.json'
        with (
            mock.patch('claude_vibeline.update.update_cache_path', return_value=cache),
            mock.patch('claude_vibeline.update.Path.mkdir', side_effect=OSError),
        ):
            write_update_cache({'latest': '9.9.9'})


class TestFetchLatestVersion:
    @responses.activate
    def test_successful_fetch(self) -> None:
        responses.add(responses.GET, PYPI_URL, json={'info': {'version': '3.0.0'}}, status=200)
        assert fetch_latest_version() == '3.0.0'

    @responses.activate
    def test_http_error(self) -> None:
        responses.add(responses.GET, PYPI_URL, status=500)
        assert fetch_latest_version() is None

    @responses.activate
    def test_invalid_json(self) -> None:
        responses.add(responses.GET, PYPI_URL, body='not json', status=200, content_type='text/plain')
        assert fetch_latest_version() is None

    @responses.activate
    def test_missing_version_field(self) -> None:
        responses.add(responses.GET, PYPI_URL, json={'info': {}}, status=200)
        assert fetch_latest_version() is None

    @responses.activate
    def test_non_string_version(self) -> None:
        responses.add(responses.GET, PYPI_URL, json={'info': {'version': 42}}, status=200)
        assert fetch_latest_version() is None


class TestParseVersion:
    def test_basic(self) -> None:
        assert _parse_version('1.2.3') == (1, 2, 3)

    def test_single(self) -> None:
        assert _parse_version('5') == (5,)

    def test_empty(self) -> None:
        assert _parse_version('') is None

    def test_non_digit(self) -> None:
        assert _parse_version('1.2.3-beta') is None

    def test_alpha_component(self) -> None:
        assert _parse_version('1.a.3') is None


class TestIsNewer:
    def test_newer_patch(self) -> None:
        assert is_newer('1.2.4', '1.2.3')

    def test_newer_minor(self) -> None:
        assert is_newer('1.3.0', '1.2.99')

    def test_newer_major(self) -> None:
        assert is_newer('2.0.0', '1.99.99')

    def test_equal(self) -> None:
        assert not is_newer('1.2.3', '1.2.3')

    def test_older(self) -> None:
        assert not is_newer('1.2.2', '1.2.3')

    def test_invalid_latest(self) -> None:
        assert not is_newer('not.a.version', '1.2.3')

    def test_invalid_current(self) -> None:
        assert not is_newer('1.2.3', 'garbage')

    def test_different_arity(self) -> None:
        assert is_newer('2.0', '1.99.99')


class TestCheckForUpdate:
    @responses.activate
    def test_new_session_with_no_prior_check_fetches(self, tmp_path: Path) -> None:
        responses.add(responses.GET, PYPI_URL, json={'info': {'version': '99.0.0'}}, status=200)
        cache = tmp_path / 'update.json'
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            result = check_for_update(is_new_session=True)
        assert result == '99.0.0'
        assert cache.exists()
        cached = json.loads(cache.read_text())
        assert cached['latest'] == '99.0.0'
        assert cached['_v'] == app_version

    @responses.activate
    def test_new_session_within_day_uses_cache(self, tmp_path: Path) -> None:
        # New session but last check was recent -> no fetch, cached latest used
        cache = tmp_path / 'update.json'
        cache.write_text(json.dumps({'latest': '99.0.0', 'checked_ts': time.time(), '_v': app_version}))
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            # No responses.add — if network is touched, test fails
            result = check_for_update(is_new_session=True)
        assert result == '99.0.0'

    @responses.activate
    def test_new_session_after_day_refetches(self, tmp_path: Path) -> None:
        responses.add(responses.GET, PYPI_URL, json={'info': {'version': '99.0.0'}}, status=200)
        cache = tmp_path / 'update.json'
        cache.write_text(
            json.dumps({'latest': '98.0.0', 'checked_ts': time.time() - UPDATE_CHECK_INTERVAL - 1, '_v': app_version})
        )
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            result = check_for_update(is_new_session=True)
        assert result == '99.0.0'

    def test_existing_session_never_fetches(self, tmp_path: Path) -> None:
        # Existing session must not trigger fetch even if cache is old
        cache = tmp_path / 'update.json'
        cache.write_text(
            json.dumps({'latest': '99.0.0', 'checked_ts': time.time() - UPDATE_CHECK_INTERVAL - 1, '_v': app_version})
        )
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            # No responses.add — if network is touched, test fails
            result = check_for_update(is_new_session=False)
        assert result == '99.0.0'

    def test_existing_session_no_cache_returns_none(self, tmp_path: Path) -> None:
        cache = tmp_path / 'update.json'
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            assert check_for_update(is_new_session=False) is None

    def test_current_version_returns_none(self, tmp_path: Path) -> None:
        cache = tmp_path / 'update.json'
        cache.write_text(json.dumps({'latest': app_version, 'checked_ts': time.time(), '_v': app_version}))
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            assert check_for_update(is_new_session=False) is None

    @responses.activate
    def test_fetch_failure_keeps_stale_latest(self, tmp_path: Path) -> None:
        responses.add(responses.GET, PYPI_URL, status=500)
        cache = tmp_path / 'update.json'
        cache.write_text(
            json.dumps({'latest': '99.0.0', 'checked_ts': time.time() - UPDATE_CHECK_INTERVAL - 1, '_v': app_version})
        )
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            result = check_for_update(is_new_session=True)
        assert result == '99.0.0'
        # checked_ts should be bumped to avoid retrying immediately after failure
        cached = json.loads(cache.read_text())
        assert cached['checked_ts'] > time.time() - 5

    @responses.activate
    def test_fetch_failure_no_cache(self, tmp_path: Path) -> None:
        responses.add(responses.GET, PYPI_URL, status=500)
        cache = tmp_path / 'update.json'
        with mock.patch('claude_vibeline.update.update_cache_path', return_value=cache):
            assert check_for_update(is_new_session=True) is None


class TestFormatUpdateMessage:
    def test_contains_versions_and_command(self) -> None:
        msg = format_update_message('99.0.0')
        assert 'update' in msg
        assert app_version in msg
        assert '99.0.0' in msg
        assert 'uv tool upgrade claude-vibeline' in msg
