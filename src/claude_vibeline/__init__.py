"""
Claude Code statusline with real subscription usage data.
"""

from importlib.metadata import version

__version__ = version('claude-vibeline')
__all__ = ['main']

from claude_vibeline.statusline import main
