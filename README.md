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
my-project │ Opus 4.7 (xhigh) │ cache ◷ 4m │ ctx 1M [###-----] 42% │
sess [##------] 19% 3h12m │ week [--------] 3% 5d20h
```

Sections wrap to multiple lines based on `--columns` width, with a trailing `│` to indicate continuation.

> **Note:** This project was developed almost entirely through AI-assisted coding with [Claude Code](https://claude.com/product/claude-code), with human oversight over all design decisions, architecture, and code review.

## Features

From Claude Code's session data:
- **Project & model** - project name, active model, and effort level (resolved from session transcript with `settings.json` fallback)
- **Context window** - how much of the context window is used, with size indicator (e.g. `200k`, `1M`)
- **Prompt cache** - 5-minute prompt cache TTL, shown as a live countdown
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

You don't have to track new releases — the statusline shows an update notification when a newer version is available on PyPI:

```
my-project │ Opus 4.7 (xhigh) │ ctx 1M [###-----] 42%
update available: 2.0.0 → 2.0.1 · uv tool upgrade claude-vibeline
```

PyPI is queried on the first render of a new session, and at most once per 24 hours overall. The cached `latest` version is reused on every render in between. Disable with `--no-update`.

The notification shares a second line below the statusline with [error messages](#error-messages); errors take precedence when both apply.

## Setup

Add to `~/.claude/settings.json`:

```jsonc
{
  // ...
  "statusLine": {
    "type": "command",
    "command": "claude-vibeline",
    "refreshIntervalSeconds": 30
  }
}
```

`refreshIntervalSeconds` tells Claude Code to re-invoke the statusline at the given interval so the prompt cache countdown stays current.

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
| `--no-session` | Hide session (5h) rate limit |
| `--no-weekly` | Hide weekly (7d) rate limit |
| `--usage-api` | Fetch per-model and extra usage from OAuth API (off by default) |
| `--opus` | Show weekly Opus rate limit (requires `--usage-api`) |
| `--sonnet` | Show weekly Sonnet rate limit (requires `--usage-api`) |
| `--extra` | Show extra usage spend (requires `--usage-api`) |
| `--no-update` | Hide update notification |
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
my-project │ Opus 4.7 (xhigh) │ cache ◷ 4m │ ctx 1M [###-----] 42% │
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
- If no token or cache exists, sections render as [pending](#pending-and-reset-states).
- The API is only called when `--usage-api` is passed with at least one of `--opus`, `--sonnet`, or `--extra`.

## Prompt cache

Tracks the 5-minute prompt cache TTL. Each user message or tool use result resets the timer. Computed from timestamps in the session transcript.

Status icons:
- `◷` — warm
- `⚠` — warm, but expiring soon
- `✗` — expired
- `↻` — expired at some point since the last user message (prefix)

The countdown is live (`◷ 4m`, `⚠ 47s`, `✗ 0s`), so set `refreshIntervalSeconds` in your statusline config to keep it current.

## Pending and reset states

Segments with no data yet or with a rolled-over window always render their label rather than disappearing. Two placeholders distinguish the states:

- `—` — **pending**. No data yet (fresh session before the first message, or API fetch failed with no cache). Applies to `sess`, `week`, `cache`, `opus`, `sonnet`, and `extra`.
- `↻` — **reset**. The rate-limit window has rolled over and a fresh number is on the way. Applies to `sess`, `week`, `opus`, `sonnet`, and `extra` (new calendar month).

`extra` is the one exception: when the API reports `is_enabled: false` or omits the field (account has no extra usage configured), the segment is omitted entirely rather than rendered as pending.

## Error messages

CLI parse errors (unknown flag, invalid value, missing argument), unexpected render failures, and malformed stdin JSON are shown on the same second line as update notifications, prefixed with the program name so it's unambiguous where the error comes from:

```
my-project │ Opus 4.7 (xhigh) │ ctx 1M [###-----] 42%
claude-vibeline: Unrecognized arguments: --bogus
```

The statusline still renders with defaults when the args are bad, so a bad flag no longer silences the output entirely. When stdin JSON is unparseable there is nothing to render and only the error message appears. Error messages are always shown — there is no opt-out.

## Session data caching

Claude Vibeline caches per-session data to avoid redundant transcript parsing on every invocation. The cache stores:

- **Effort level** — the resolved effort and the timestamp of the latest transcript entry processed, so subsequent invocations only scan new entries instead of re-reading the entire transcript.
- **Last user message timestamp** — used by the prompt cache countdown as a fallback when the transcript cannot be read.

Stale session files (older than 30 days) are cleaned up whenever a new session writes its first cache entry.

All locally cached data (usage responses, session state, update check) is version-stamped and automatically invalidated on upgrade.

## Limitations

- **Undocumented API** — the OAuth usage endpoint is undocumented and may break without notice.
- **Limited stdin data** — the statusline process receives only a JSON blob on stdin. Claude Code's own CLI arguments (e.g. `--model`) and internal environment variables are not accessible.
- **Effort level is inferred** — effort is not provided in stdin. It is resolved from the session transcript by scanning for `/model` and `/effort` commands, with a `settings.json` fallback. After session resume, effort is shown with a `?` suffix until `/effort` or `/model` is used.
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
