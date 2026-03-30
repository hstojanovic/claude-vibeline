import dataclasses
from typing import Annotated

import cappa

from claude_vibeline import __doc__ as description, __version__ as app_version


@dataclasses.dataclass
@cappa.command(name='claude-vibeline', description=description)
class Args:
    columns: Annotated[int, cappa.Arg(long='--columns', help='terminal width in characters')] = 80
    bar_width: Annotated[int, cappa.Arg(long='--bar-width', help='progress bar width in characters')] = 8
    currency: Annotated[str, cappa.Arg(long='--currency', help='currency symbol for extra usage')] = '€'
    project: Annotated[bool, cappa.Arg(long='--no-project', help='hide project name', show_default=False)] = True
    model: Annotated[bool, cappa.Arg(long='--no-model', help='hide model and effort level', show_default=False)] = True
    cache: Annotated[bool, cappa.Arg(long='--no-cache', help='hide prompt cache status', show_default=False)] = True
    refresh: Annotated[
        bool,
        cappa.Arg(
            long='--cache-updater', help='spawn background process to refresh cache countdown', show_default=False
        ),
    ] = False
    context: Annotated[
        bool, cappa.Arg(long=['--no-context', '--no-ctx'], help='hide context window usage', show_default=False)
    ] = True
    session: Annotated[
        bool,
        cappa.Arg(
            long=['--no-session', '--no-sess', '--no-5h'], help='hide session (5h) rate limit', show_default=False
        ),
    ] = True
    weekly: Annotated[
        bool,
        cappa.Arg(long=['--no-weekly', '--no-week', '--no-7d'], help='hide weekly (7d) rate limit', show_default=False),
    ] = True
    usage: Annotated[
        bool, cappa.Arg(long='--usage-api', help='fetch per-model and extra usage from OAuth API', show_default=False)
    ] = False
    opus: Annotated[
        bool, cappa.Arg(long='--opus', help='show weekly Opus rate limit (requires --usage-api)', show_default=False)
    ] = False
    sonnet: Annotated[
        bool,
        cappa.Arg(long='--sonnet', help='show weekly Sonnet rate limit (requires --usage-api)', show_default=False),
    ] = False
    extra: Annotated[
        bool, cappa.Arg(long='--extra', help='show extra usage spend (requires --usage-api)', show_default=False)
    ] = False
    debug: Annotated[bool, cappa.Arg(long='--debug', help='log each output to debug file', show_default=False)] = False

    version: Annotated[
        str,
        cappa.Arg(
            app_version,
            short='-v',
            long='--version',
            action=cappa.ArgAction.version,
            help='Show version and exit.',
            group=cappa.Group(name='Help', section=2),
        ),
    ] = app_version
