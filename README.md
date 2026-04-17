# Claude Vibeline

[![CI](https://github.com/hstojanovic/claude-vibeline/actions/workflows/ci.yml/badge.svg)](https://github.com/hstojanovic/claude-vibeline/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/hstojanovic/claude-vibeline/graph/badge.svg)](https://codecov.io/gh/hstojanovic/claude-vibeline)
[![PyPI](https://img.shields.io/pypi/v/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![Downloads](https://img.shields.io/pypi/dm/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![Python](https://img.shields.io/pypi/pyversions/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![License](https://img.shields.io/pypi/l/claude-vibeline)](https://github.com/hstojanovic/claude-vibeline/blob/master/LICENSE)
[![Built with Claude Code](https://img.shields.io/badge/Built%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/product/claude-code)

A custom statusline for [Claude Code](https://claude.com/product/claude-code) that shows session details, prompt cache status, and rate limits — designed for Pro, Max, and Team users.

```
my-project │ Opus 4.7 (xhigh) │ cache ◷ 14:35 │ ctx 1M [###-----] 42% │
sess [##------] 19% 3h12m │ week [--------] 3% 5d20h
```

Sections wrap to multiple lines based on `--columns` width, with a trailing `│` to indicate continuation.

> **Note:** This project was developed almost entirely through AI-assisted coding with [Claude Code](https://claude.com/product/claude-code), with human oversight over all design decisions, architecture, and code review.

## Features

From Claude Code's session data:
- **Project & model** - project name, active model, and effort level (resolved from session transcript with `settings.json` fallback)
- **Context window** - how much of the context window is used, with size indicator (e.g. `200k`, `1M`)
- **Prompt cache** - 5-minute prompt cache TTL, shown as an expiration clock time or a live countdown with `--cache-updater`
- **Session limit** - 5-hour rate limit utilization with reset countdown
- **Weekly limit** - 7-day rate limit utilization with reset countdown

Opt-in via `--usage-api` (from Anthropic's OAuth API):
- **Per-model limits** - weekly Opus and Sonnet limits with reset countdowns
- **Extra usage** - spend against your monthly extra usage cap with reset countdown

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
| `--cache-updater` | Spawn background process to refresh cache countdown (off by default) |
| `--no-context` | Hide context window usage |
| `--no-session` | Hide session (5h) rate limit |
| `--no-weekly` | Hide weekly (7d) rate limit |
| `--usage-api` | Fetch per-model and extra usage from OAuth API (off by default) |
| `--opus` | Show weekly Opus rate limit (requires `--usage-api`) |
| `--sonnet` | Show weekly Sonnet rate limit (requires `--usage-api`) |
| `--extra` | Show extra usage spend (requires `--usage-api`) |
| `--debug` | Log each statusline output to debug file |

Example with all API sections enabled:

```jsonc
{
  // ...
  "statusLine": {
    "type": "command",
    "command": "claude-vibeline --usage-api --opus --sonnet --extra --currency $"
  }
}
```

```
my-project │ Opus 4.7 (xhigh) │ cache ◷ 14:35 │ ctx 1M [###-----] 42% │
sess [##------] 19% 3h12m │ week [--------] 3% 5d20h │
opus [#-------] 10% 5d20h │ sonnet [--------] 2% 5d20h │
extra 1.23/20$ 7d0h
```

## Rate limits

### Stdin rate limits

Session and weekly limits are read directly from Claude Code's session data — no API call or authentication needed. Shown by default; disable with `--no-session` or `--no-weekly`.

### OAuth API usage

Per-model and extra usage data is fetched from an undocumented Anthropic OAuth endpoint (see [Limitations](#limitations)).

- Requires a valid OAuth token from a Claude Pro, Max, or Team subscription.
- Responses are cached locally for 60 seconds, and the cache is reused when the token expires or the API is unavailable.
- If no token or cache exists, these sections are omitted.
- The API is only called when `--usage-api` is passed with at least one of `--opus`, `--sonnet`, or `--extra`.

## Prompt cache

Tracks the 5-minute prompt cache TTL. Each user message or tool use result resets the timer. Computed from timestamps in the session transcript.

Status icons:
- `◷` — warm
- `⚠` — warm, but expiring soon
- `✗` — expired
- `↻` — expired at some point since the last user message (prefix)

By default, the time is an absolute clock time — upcoming expiry for warm caches (`◷ 14:35`), past expiry for expired ones (`✗ 14:30`). With `--cache-updater`, it becomes a live countdown (`◷ 4m`, `✗ 0s`).

## Session data caching

Claude Vibeline caches per-session data to avoid redundant transcript parsing on every invocation. The cache stores:

- **Effort level** — the resolved effort and the timestamp of the latest transcript entry processed, so subsequent invocations only scan new entries instead of re-reading the entire transcript.
- **Last user message timestamp** — used by the prompt cache countdown as a fallback when the transcript cannot be read.

Stale session files (older than 30 days) are cleaned up on every write.

## Cache updater

This feature is **off by default** because it modifies your `~/.claude/settings.json`.

When enabled with `--cache-updater`, Claude Vibeline spawns a background process that toggles a trailing space in the `statusLine.command` value every 30 seconds. This causes Claude Code to re-invoke the statusline and redraw the cache countdown. The background process runs until the cache expires and then cleans up after itself.

Since Claude Code does not provide a push mechanism for statusline updates, this settings toggle is used to force a re-render.

Because this edits your settings file, be aware that:

- Concurrent manual edits to `settings.json` while the cache updater is running could conflict (writes are atomic, but a read-modify-write race is possible).
- Tools that watch `settings.json` for changes will see repeated modifications.

To enable:

```jsonc
{
  // ...
  "statusLine": {
    "type": "command",
    "command": "claude-vibeline --cache-updater"
  }
}
```

Without `--cache-updater`, the cache section shows the expiration clock time — an absolute value that remains accurate without re-invocation. The live countdown (`◷ 4m`) requires the updater to stay current.

## Local data

All locally cached data (usage responses, session state, updater lock) is version-stamped and automatically invalidated on upgrade.

## Limitations

- **Undocumented APIs** — the OAuth usage endpoint is undocumented and may break without notice. The cache updater mechanism (see [above](#cache-updater)) relies on undocumented Claude Code behavior and may also break.
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
