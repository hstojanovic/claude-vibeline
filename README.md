# Claude Vibeline

[![CI](https://github.com/hstojanovic/claude-vibeline/actions/workflows/ci.yml/badge.svg)](https://github.com/hstojanovic/claude-vibeline/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/hstojanovic/claude-vibeline/graph/badge.svg)](https://codecov.io/gh/hstojanovic/claude-vibeline)
[![PyPI](https://img.shields.io/pypi/v/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![Downloads](https://img.shields.io/pypi/dm/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![Python](https://img.shields.io/pypi/pyversions/claude-vibeline)](https://pypi.org/project/claude-vibeline/)
[![License](https://img.shields.io/pypi/l/claude-vibeline)](https://github.com/hstojanovic/claude-vibeline/blob/master/LICENSE)
[![Built with Claude Code](https://img.shields.io/badge/Built%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/product/claude-code)

A custom statusline for [Claude Code](https://claude.com/product/claude-code) that shows real **subscription** usage data from the Anthropic API - designed for Pro, Max, and Team users.

Unlike token-based cost trackers, Claude Vibeline shows your actual rate limit utilization and reset countdowns as reported by Anthropic.

```
my-project │ Opus 4.6 (high) │ cache ● 14:32 │ ctx [###-----] 42% │
sess [##------] 19% 3h12m │ week [--------] 3% 5d20h │ extra 1.23/20€ 7d0h
```

Sections automatically wrap to multiple lines based on terminal width, with a trailing `│` to indicate continuation.

> **Note:** This project was developed almost entirely through AI-assisted coding with [Claude Code](https://claude.com/product/claude-code), with human oversight over all design decisions, architecture, and code review.

## Features

From Claude Code's session data:
- **Project & model** - project name, active model, and effort level
- **Context window** - how much of the context window is used
- **Prompt cache** - tracks Anthropic's 5-minute prompt cache TTL for subscription users. Computed from user message timestamps in the session transcript:
  - `● 14:32` — cache is warm, expires at 14:32
  - `↻ 14:32` — cache expired after the last user message but has been refreshed
  - `○ 14:27` — cache expired at 14:27

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
my-project │ Opus 4.6 (high) │ ctx [##---] 42% │ sess [#----] 19% 3h12m │
week [-----] 3% 5d20h │ extra 1.23/20$ 7d0h
```

If everything fits on a single line, no wrapping occurs and no trailing `│` is shown.

## Usage data

Usage data is fetched from an **undocumented, unstable** Anthropic OAuth endpoint. It requires a valid OAuth token from a Claude Pro, Max, or Team subscription.

Responses are cached locally for 60 seconds. Cached usage data is used when the token expires or the API is unavailable. If no token or cache exists, usage sections are omitted. Use `--no-usage` to disable API calls entirely.

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

## License

This project is licensed under the [MIT](https://github.com/hstojanovic/claude-vibeline/blob/master/LICENSE) license.

---

Claude Vibeline is an independent project and is not affiliated with or endorsed by Anthropic.
