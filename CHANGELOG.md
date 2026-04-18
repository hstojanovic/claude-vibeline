# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Common Changelog](https://common-changelog.org/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Change cache section to always render as a live countdown (`◷ 4m`, `⚠ 47s`, `✗ 0s`); absolute clock-time display has been dropped
- Skip session cache writes when the last user timestamp is unchanged, and defer stale-file cleanup to new-session writes — avoids redundant I/O when Claude Code refreshes the statusline on an interval

### Removed

- **Breaking:** Remove `--cache-updater` flag and the background process that toggled `settings.json` to force statusline re-renders; Claude Code now exposes `refreshIntervalSeconds` in the `statusLine` config for native refreshes

## [2.0.0] - 2026-04-17

### Changed

- **Breaking:** Rename `--no-usage` to `--usage-api`, off by default
- **Breaking:** Replace `--no-opus`, `--no-sonnet`, `--no-extra` with `--opus`, `--sonnet`, `--extra` (all opt-in, require `--usage-api`)
- **Breaking:** Rename `--no-refresh` to `--cache-updater`, off by default
- Change session and weekly rate limits to read from stdin `rate_limits` instead of the OAuth API; these sections no longer require a subscription token
- Change cache icon from `✓` to `◷` and default display from live countdown (`✓ 4m`) to absolute clock time (`◷ 14:35` upcoming, `✗ 14:30` past); live countdown is opt-in via `--cache-updater`
- Change cache gap indicator from `!↻` to `↻`

### Added

- Add Opus 4.7 support with `xhigh` effort level; when settings specify `xhigh` on a model that doesn't support it (e.g. Opus 4.6, Sonnet 4.6), the fallback shown is `high?` instead of `medium?`

### Fixed

- Fix effort level showing on legacy models (Opus 4.5, Sonnet 4.5, etc.) that don't support adaptive thinking
- Fix orphaned temp files left behind during debug log rotation
- Fix effort display when the transcript's last effort isn't supported on the current model (e.g. switching Opus with `max` effort back to Sonnet): now falls through to the `settings.json` value with `?` suffix instead of defaulting to `(medium?)`

## [1.1.0] - 2026-03-17

### Changed

- Change prompt cache display from time-based (`● 21:35`) to countdown (`✓ 4m`) with low-threshold warning (`⚠ 47s`) and expired (`✗`) states
- Change session cache writes to merge fields atomically instead of full replacement

### Added

- Add `max` effort level support (Opus 4.6 only)
- Add effort resolution from session transcript (`/model`, `/effort`, `/effort auto` commands) with incremental scanning, per-session caching, and `settings.json` fallback (shown with `?` suffix)
- Add effort invalidation after session resume (detected via `<synthetic>` transcript entries)
- Add per-model supported effort levels (Opus: low/medium/high/max, Sonnet: low/medium/high)
- Add automatic cleanup of session cache files older than 30 days
- Add context window size display (e.g. `200k`, `1M`) from stdin `context_window_size` field
- Add background cache timer countdown with `--no-refresh` flag to disable
- Add version validation on all cache and lock files
- Add atomic file writes across session cache, settings toggle, refresh lock, and debug log

### Fixed

- Fix usage showing stale percentage with `0m` countdown when `resets_at` is in the past
- Fix debug log truncation corrupting JSONL structure by cutting at arbitrary byte offsets

## [1.0.0] - 2026-03-12

### Added

- Add project name, model, and effort level display from Claude Code stdin JSON
- Add context window usage display
- Add prompt cache status with three states: `●` warm, `↻` recached after gap, `○` expired
- Add session and weekly rate limit utilization with reset countdowns via Anthropic OAuth API
- Add per-model weekly usage (Opus, Sonnet) sections
- Add extra usage spend tracking with monthly reset countdown
- Add 60-second response cache with negative caching on failure
- Add OAuth token auto-discovery from `~/.claude/.credentials.json` and macOS Keychain
- Add automatic multi-line wrapping based on `--columns` width, with trailing `│` on wrapped lines
- Add CLI flags to toggle each section (`--no-project`, `--no-model`, `--no-cache`, `--no-context`, `--no-session`, `--no-weekly`, `--no-opus`, `--no-sonnet`, `--no-extra`) with short aliases
- Add `--no-usage` flag to skip fetching usage data entirely
- Add `--columns` option for terminal width (default: 80)
- Add `--bar-width` option for progress bar width (default: 8)
- Add `--currency` option for extra usage currency symbol (default: `€`)
- Add `--debug` flag to log each statusline output to a platform-specific log file

[Unreleased]: https://github.com/hstojanovic/claude-vibeline/compare/1.1.0...HEAD
[1.1.0]: https://github.com/hstojanovic/claude-vibeline/compare/1.0.0...1.1.0
[1.0.0]: https://github.com/hstojanovic/claude-vibeline/releases/tag/1.0.0
