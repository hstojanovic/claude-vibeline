import json
import threading
import time
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

from claude_vibeline import __version__ as app_version
from claude_vibeline.refresh import (
    is_lock_owner,
    maybe_spawn_cache_updater,
    refresh_lock_path,
    run_refresh_loop,
    spawn_cache_updater,
    toggle_settings_space,
)


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

    def test_command_not_string(self, tmp_path: Path) -> None:
        settings = tmp_path / 'settings.json'
        data = {'statusLine': {'type': 'command', 'command': 42}}
        settings.write_text(json.dumps(data), encoding='utf-8')
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            toggle_settings_space()
        assert json.loads(settings.read_text())['statusLine']['command'] == 42


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
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        toggles: list[int] = []
        monkeypatch.setattr('claude_vibeline.refresh.toggle_settings_space', lambda: toggles.append(1))
        with mock.patch('time.sleep'):
            run_refresh_loop(time.time() - 1, self.TOKEN)
        assert len(toggles) == 0
        assert not lock.exists()

    def test_exits_when_lock_taken_mid_loop(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': self.TOKEN, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        toggles: list[int] = []

        def toggle_and_steal_lock() -> None:
            toggles.append(1)
            lock.write_text(json.dumps({'token': 'new-owner'}))

        monkeypatch.setattr('claude_vibeline.refresh.toggle_settings_space', toggle_and_steal_lock)
        times = iter([100.0, 130.0])
        monkeypatch.setattr(time, 'time', lambda: next(times))
        with mock.patch('time.sleep'):
            run_refresh_loop(200.0, self.TOKEN)
        assert len(toggles) == 1
        assert lock.exists()

    def test_loop_toggles_and_cleans_up(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text(json.dumps({'token': self.TOKEN, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        toggles: list[int] = []
        monkeypatch.setattr('claude_vibeline.refresh.toggle_settings_space', lambda: toggles.append(1))
        # time.time() called once per iteration (remaining calc)
        # iter 1: 150-100=50>0 -> toggle; iter 2: 150-130=20>0 -> toggle
        # iter 3: 150-160<0 -> break (no extra toggle)
        times = iter([100.0, 130.0, 160.0])
        monkeypatch.setattr(time, 'time', lambda: next(times))
        with mock.patch('time.sleep'):
            run_refresh_loop(150.0, self.TOKEN)
        assert len(toggles) == 2
        assert not lock.exists()


class TestSpawnCacheUpdater:
    def test_spawns_and_writes_lock(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: tmp_path / 'refresh.lock')
        mock_proc = mock.MagicMock(pid=12345)
        with mock.patch('claude_vibeline.refresh.sp.Popen', return_value=mock_proc) as popen:
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
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        with mock.patch('claude_vibeline.refresh.sp.Popen') as popen:
            spawn_cache_updater(expiry)
            popen.assert_not_called()
        assert json.loads(lock.read_text())['token'] == 'old'

    def test_skips_when_expiry_earlier(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        expiry = time.time() + 300
        lock.write_text(json.dumps({'token': 'old', 'expiry': expiry, '_v': app_version}))
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        with mock.patch('claude_vibeline.refresh.sp.Popen') as popen:
            spawn_cache_updater(expiry - 5)
            popen.assert_not_called()

    def test_spawns_when_expiry_changed(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        old_expiry = time.time() + 100
        new_expiry = time.time() + 400
        lock.write_text(json.dumps({'token': 'old', 'expiry': old_expiry}))
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        mock_proc = mock.MagicMock(pid=55555)
        with mock.patch('claude_vibeline.refresh.sp.Popen', return_value=mock_proc):
            spawn_cache_updater(new_expiry)
        data = json.loads(lock.read_text())
        assert data['token'] != 'old'
        assert data['expiry'] == new_expiry

    def test_spawns_when_no_lock(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: tmp_path / 'refresh.lock')
        mock_proc = mock.MagicMock(pid=12345)
        with mock.patch('claude_vibeline.refresh.sp.Popen', return_value=mock_proc):
            spawn_cache_updater(time.time() + 300)
        assert (tmp_path / 'refresh.lock').exists()

    def test_spawns_when_corrupt_lock(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        lock.write_text('{bad')
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        mock_proc = mock.MagicMock(pid=12345)
        with mock.patch('claude_vibeline.refresh.sp.Popen', return_value=mock_proc):
            spawn_cache_updater(time.time() + 300)
        assert 'token' in json.loads(lock.read_text())

    def test_handles_oserror(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: tmp_path / 'refresh.lock')
        with mock.patch('claude_vibeline.refresh.sp.Popen', side_effect=OSError):
            spawn_cache_updater(time.time() + 300)
        assert not (tmp_path / 'refresh.lock').exists()

    def test_unix_sets_start_new_session(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: tmp_path / 'refresh.lock')
        monkeypatch.setattr('claude_vibeline.refresh.sys.platform', 'linux')
        mock_proc = mock.MagicMock(pid=12345)
        with mock.patch('claude_vibeline.refresh.sp.Popen', return_value=mock_proc) as popen:
            spawn_cache_updater(time.time() + 300)
        assert popen.call_args[1]['start_new_session'] is True
        assert 'creationflags' not in popen.call_args[1]


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
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        monkeypatch.setattr('claude_vibeline.refresh.REFRESH_INTERVAL', 0.1)
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
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        monkeypatch.setattr('claude_vibeline.refresh.REFRESH_INTERVAL', 0.2)
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
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        with mock.patch('claude_vibeline.refresh.sp.Popen') as popen:
            spawn_cache_updater(expiry)
            popen.assert_not_called()

    def test_different_expiry_does_respawn(self, tmp_path: Path, monkeypatch: Any) -> None:
        lock = tmp_path / 'refresh.lock'
        old_expiry = time.time() + 100
        lock.write_text(json.dumps({'token': 'old', 'expiry': old_expiry}))
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        mock_proc = mock.MagicMock(pid=55555)
        with mock.patch('claude_vibeline.refresh.sp.Popen', return_value=mock_proc):
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
        monkeypatch.setattr('claude_vibeline.refresh.refresh_lock_path', lambda: lock)
        monkeypatch.setattr('claude_vibeline.refresh.REFRESH_INTERVAL', 0.1)
        with mock.patch.object(Path, 'expanduser', return_value=settings):
            t = threading.Thread(target=run_refresh_loop, args=(time.time() + 0.2, token))
            t.start()
            t.join(timeout=5)
        assert not lock.exists()


class TestRefreshLockPath:
    def test_returns_expected_path(self) -> None:
        path = refresh_lock_path()
        assert isinstance(path, Path)
        assert path.name == 'refresh.lock'
        assert 'claude-vibeline' in str(path)


class TestMaybeSpawnCacheUpdater:
    def test_none_timestamp_skips(self) -> None:
        with mock.patch('claude_vibeline.refresh.spawn_cache_updater') as mock_spawn:
            maybe_spawn_cache_updater(None)
        mock_spawn.assert_not_called()

    def test_expired_timestamp_skips(self) -> None:
        with mock.patch('claude_vibeline.refresh.spawn_cache_updater') as mock_spawn:
            maybe_spawn_cache_updater(time.time() - 600)
        mock_spawn.assert_not_called()

    def test_active_timestamp_spawns(self) -> None:
        with mock.patch('claude_vibeline.refresh.spawn_cache_updater') as mock_spawn:
            maybe_spawn_cache_updater(time.time())
        mock_spawn.assert_called_once()
