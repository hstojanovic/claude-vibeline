# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Common Changelog](https://common-changelog.org/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/hstojanovic/claude-vibeline/compare/1.0.0...HEAD
[1.0.0]: https://github.com/hstojanovic/claude-vibeline/releases/tag/1.0.0
