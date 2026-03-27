# Claude Vibeline

[![CI](https://github.com/hstojanovic/claude-vibeline/actions/workflows/ci.yml/badge.svg)](https://github.com/hstojanovic/claude-vibeline/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/hstojanovic/claude-vibeline/graph/badge.svg)](https://codecov.io/gh/hstojanovic/claude-vibeline)
[![PyPI](https://img.shields.io/pypi/v/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![Downloads](https://img.shields.io/pypi/dm/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![Python](https://img.shields.io/pypi/pyversions/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![License](https://img.shields.io/pypi/l/claude-vibeline)](https://github.com/hstojanovic/claude-vibeline/blob/master/LICENSE)
[![Built with Claude Code](https://img.shields.io/badge/Built%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/product/claude-code)

A custom statusline for [Claude Code](https://claude.com/product/claude-code) that shows session details, prompt cache status, and real **subscription** usage data — designed for Pro, Max, and Team users.

Unlike token-based cost trackers, Claude Vibeline shows your actual rate limit utilization and reset countdowns as reported by Anthropic, alongside context window usage, effort level, and a live cache countdown.

```
my-project │ Opus 4.6 (high) │ cache ✓ 4m │ ctx 200k [###-----] 42% │
sess [##------] 19% 3h12m │ week [--------] 3% 5d20h │ extra 1.23/20€ 7d0h
```

Sections wrap to multiple lines based on `--columns` width, with a trailing `│` to indicate continuation.

> **Note:** This project was developed almost entirely through AI-assisted coding with [Claude Code](https://claude.com/product/claude-code), with human oversight over all design decisions, architecture, and code review.

## Features

From Claude Code's session data:
- **Project & model** - project name, active model, and effort level (resolved from session transcript with `settings.json` fallback)
- **Context window** - how much of the context window is used, with size indicator (e.g. `200k`, `1M`)
- **Prompt cache** - live countdown of the 5-minute prompt cache TTL (see [Prompt cache](#prompt-cache))

From Anthropic's OAuth API (subscription rate limits, not per-token costs):
- **Session limit** - 5-hour rate limit utilization with reset countdown
- **Weekly limit** - 7-day rate limit utilization with reset countdown
- **Per-model limits** - weekly Opus and Sonnet limits with reset countdowns, shown when applicable
- **Extra usage** - spend against your monthly extra usage cap with reset countdown, shown only if enabled

Every section is individually toggleable via CLI flags.

## Installation

Available on [PyPI](https://pypi.org/project/claude-vibeline/). We recommend using [uv](https://docs.astral.sh/uv/getting-started/installation/).

To install:

```bash
uv tool install claude-vibeline
```

To update:

```bash
uv tool upgrade claude-vibeline
```

## Setup

Add to `~/.claude/settings.json`:

```jsonc
{
  // ...
  "statusLine": {
    "type": "command",
    "command": "claude-vibeline"
  }
}
```

## Options

| Flag | Description |
|------|-------------|
| `--columns N` | Terminal width in characters (default: 80) |
| `--bar-width N` | Progress bar width in characters (default: 8) |
| `--currency S` | Currency symbol for extra usage (default: `€`) |
| `--no-project` | Hide project name |
| `--no-model` | Hide model and effort level |
| `--no-cache` | Hide prompt cache status |
| `--no-refresh` | Disable background cache timer refresh |
| `--no-context` | Hide context window usage |
| `--no-usage` | Skip fetching usage data entirely |
| `--no-session` | Hide session (5h) usage |
| `--no-weekly` | Hide weekly (7d) usage |
| `--no-opus` | Hide weekly Opus usage |
| `--no-sonnet` | Hide weekly Sonnet usage |
| `--no-extra` | Hide extra usage spend |
| `--debug` | Log each statusline output to debug file |

Example with customizations:

```jsonc
{
  // ...
  "statusLine": {
    "type": "command",
    "command": "claude-vibeline --bar-width 5 --currency $ --no-cache --no-opus --no-sonnet"
  }
}
```

```
my-project │ Opus 4.6 (high) │ ctx 200k [##---] 42% │ sess [#----] 19% 3h12m │
week [-----] 3% 5d20h │ extra 1.23/20$ 7d0h
```

## Usage data

Usage data is fetched from an Anthropic OAuth endpoint (see [Limitations](#limitations)). It requires a valid OAuth token from a Claude Pro, Max, or Team subscription.

Responses are cached locally for 60 seconds. Cached usage data is used when the token expires or the API is unavailable. If no token or cache exists, usage sections are omitted. Use `--no-usage` to disable API calls entirely.

## Prompt cache

Tracks the 5-minute prompt cache TTL. Each user message or tool use result resets the timer. Computed from timestamps in the session transcript.

Status icons:
- `✓` — warm
- `⚠` — warm, but expiring soon
- `✗` — expired
- `!` — expired at some point since the last user message

See [Cache timer refresh](#cache-timer-refresh) for how the countdown stays live between invocations.

## Session data caching

Claude Vibeline caches per-session data to avoid redundant transcript parsing on every invocation. The cache stores:

- **Effort level** — the resolved effort and the timestamp of the latest transcript entry processed, so subsequent invocations only scan new entries instead of re-reading the entire transcript.
- **Last user message timestamp** — used by the prompt cache countdown as a fallback when the transcript cannot be read.

Stale session files (older than 30 days) are cleaned up on every write.

## Cache timer refresh

To keep the cache countdown updating in real time, Claude Vibeline spawns a background process that **modifies your `~/.claude/settings.json`** every 30 seconds. It toggles a trailing space in the `statusLine.command` value, which causes Claude Code to re-invoke the statusline and redraw the countdown. The background process runs until the cache expires and then cleans up after itself.

Since Claude Code does not provide a push mechanism for statusline updates, this settings toggle is used to force a re-render.

Because this edits your settings file, be aware that:

- Concurrent manual edits to `settings.json` while the refresh process is running could conflict (writes are atomic, but a read-modify-write race is possible).
- Tools that watch `settings.json` for changes will see repeated modifications.

To disable this behavior, pass `--no-refresh`:

```jsonc
{
  // ...
  "statusLine": {
    "type": "command",
    "command": "claude-vibeline --no-refresh"
  }
}
```

With `--no-refresh`, the cache section still appears but only updates when Claude Code naturally re-invokes the statusline (e.g., after each assistant response).

## Local data

All locally cached data (usage responses, session state, refresh lock) is version-stamped and automatically invalidated on upgrade.

## Limitations

- **Undocumented APIs** — the OAuth usage endpoint is undocumented and may break without notice. The cache timer refresh mechanism (see [above](#cache-timer-refresh)) relies on undocumented Claude Code behavior and may also break.
- **Limited stdin data** — the statusline process receives only a JSON blob on stdin. Claude Code's own CLI arguments (e.g. `--model`) and internal environment variables are not accessible.
- **Effort level is inferred** — effort is not provided in stdin. It is resolved from the session transcript by scanning for `/model` and `/effort` commands, with a `settings.json` fallback shown with `?` suffix. After session resume, effort resets to the `?` fallback until `/effort` or `/model` is used.
- **No session fork support** — forked sessions share a transcript file. The prompt cache countdown and effort detection may be inaccurate because messages from all forks are interleaved.
- **No subagent tracking** — subagents run in separate sessions with their own prompt cache, but the statusline only tracks the main session's cache.

## Development

Requires [uv](https://docs.astral.sh/uv/).

Clone and setup:

```bash
git clone https://github.com/hstojanovic/claude-vibeline.git
cd claude-vibeline
uv sync
```

Run checks:

```bash
uv run ruff format --check
uv run ruff check
uv run ty check
uv run pytest --cov
```

Build:

```bash
uv build
```

Pass `--debug` to log each statusline invocation as JSONL, including the stdin input, parsed arguments, resolved effort, usage data, and rendered output.

## License

This project is licensed under the [MIT](https://github.com/hstojanovic/claude-vibeline/blob/master/LICENSE) license.

---

Claude Vibeline is an independent project and is not affiliated with or endorsed by Anthropic.
