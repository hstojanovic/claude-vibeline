import json
import subprocess as sp
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import responses

from claude_vibeline import __version__ as app_version
from claude_vibeline.constants import CACHE_TTL_SECONDS, USAGE_URL
from claude_vibeline.usage import cache_path, fetch_usage, read_oauth_token, token_from_entry, write_usage_cache

if TYPE_CHECKING:
    from claude_vibeline.schema import UsageData


class TestCachePath:
    def test_returns_expected_path(self) -> None:
        path = cache_path()
        assert isinstance(path, Path)
        assert path.name == 'usage.json'
        assert 'claude-vibeline' in str(path)


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
            mock.patch('claude_vibeline.usage.sys.platform', 'darwin'),
            mock.patch('claude_vibeline.usage.sp.run') as mock_run,
        ):
            mock_run.return_value = mock.Mock(stdout=keychain_data + '\n')
            assert read_oauth_token() == 'keychain_tok'

    def test_macos_keychain_error(self, tmp_path: Path) -> None:
        missing = tmp_path / 'nonexistent.json'
        with (
            mock.patch.object(Path, 'expanduser', return_value=missing),
            mock.patch('claude_vibeline.usage.sys.platform', 'darwin'),
            mock.patch('claude_vibeline.usage.sp.run', side_effect=sp.CalledProcessError(1, 'security')),
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
        with mock.patch('claude_vibeline.usage.Path.mkdir', side_effect=OSError):
            write_usage_cache(cache, {'five_hour': {'utilization': 0}})


class TestFetchUsage:
    @responses.activate
    def test_successful_api_call(self, tmp_path: Path) -> None:
        api_data = {'five_hour': {'utilization': 42, 'resets_at': '2099-01-01T00:00:00+00:00'}}
        responses.add(responses.GET, USAGE_URL, json=api_data, status=200)

        cache = tmp_path / 'usage.json'
        with (
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value='tok'),
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
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result == fresh_data
        assert stale_ts is None

    def test_cached_response_within_ttl(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        cached = {'five_hour': {'utilization': 10}, '_ts': time.time(), '_v': app_version}
        cache.write_text(json.dumps(cached))

        with mock.patch('claude_vibeline.usage.cache_path', return_value=cache):
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
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value='tok'),
        ):
            result, stale_ts = fetch_usage()

        assert result == fresh_data
        assert stale_ts is None

    def test_no_token(self, tmp_path: Path) -> None:
        cache = tmp_path / 'usage.json'
        with (
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value=None),
        ):
            result, stale_ts = fetch_usage()

        assert result is None
        assert stale_ts is None

    @responses.activate
    def test_api_error_returns_none_and_caches_negative(self, tmp_path: Path) -> None:
        responses.add(responses.GET, USAGE_URL, status=500)

        cache = tmp_path / 'usage.json'
        with (
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value='tok'),
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
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value='tok'),
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
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value='tok'),
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
            mock.patch('claude_vibeline.usage.cache_path', return_value=cache),
            mock.patch('claude_vibeline.usage.read_oauth_token', return_value=None),
        ):
            result, stale_ts = fetch_usage()

        assert result is not None
        assert result['five_hour']['utilization'] == 30
        assert isinstance(stale_ts, float)
