from typing import TypedDict


class UsageBucket(TypedDict, total=False):
    utilization: int | None
    resets_at: str


class ExtraUsage(TypedDict, total=False):
    is_enabled: bool
    used_credits: int
    monthly_limit: int


class UsageData(TypedDict, total=False):
    five_hour: UsageBucket
    seven_day: UsageBucket
    seven_day_opus: UsageBucket
    seven_day_sonnet: UsageBucket
    extra_usage: ExtraUsage


class OAuthEntry(TypedDict, total=False):
    accessToken: str
    refreshToken: str
    expiresAt: int


class OAuthCredentials(TypedDict, total=False):
    claudeAiOauth: OAuthEntry


class StdinRateLimitBucket(TypedDict, total=False):
    used_percentage: float
    resets_at: int


class StdinRateLimits(TypedDict, total=False):
    five_hour: StdinRateLimitBucket
    seven_day: StdinRateLimitBucket


class Workspace(TypedDict, total=False):
    project_dir: str
    current_dir: str


class Model(TypedDict, total=False):
    display_name: str


class ContextWindow(TypedDict, total=False):
    used_percentage: float
    context_window_size: int


class StdinData(TypedDict, total=False):
    workspace: Workspace
    model: Model
    context_window: ContextWindow
    transcript_path: str
    session_id: str
    rate_limits: StdinRateLimits


class SessionCache(TypedDict, total=False):
    effort: str
    effort_ts: str
    last_user_ts: float
