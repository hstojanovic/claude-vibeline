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
        bool, cappa.Arg(long='--no-refresh', help='disable background cache timer refresh', show_default=False)
    ] = True
    context: Annotated[
        bool, cappa.Arg(long=['--no-context', '--no-ctx'], help='hide context window usage', show_default=False)
    ] = True
    usage: Annotated[
        bool, cappa.Arg(long='--no-usage', help='skip fetching usage data entirely', show_default=False)
    ] = True
    session: Annotated[
        bool,
        cappa.Arg(long=['--no-session', '--no-sess', '--no-5h'], help='hide session (5h) usage', show_default=False),
    ] = True
    weekly: Annotated[
        bool, cappa.Arg(long=['--no-weekly', '--no-week', '--no-7d'], help='hide weekly (7d) usage', show_default=False)
    ] = True
    opus: Annotated[bool, cappa.Arg(long='--no-opus', help='hide weekly Opus usage', show_default=False)] = True
    sonnet: Annotated[bool, cappa.Arg(long='--no-sonnet', help='hide weekly Sonnet usage', show_default=False)] = True
    extra: Annotated[bool, cappa.Arg(long='--no-extra', help='hide extra usage spend', show_default=False)] = True
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
