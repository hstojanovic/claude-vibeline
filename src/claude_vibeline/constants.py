import re

# Colors (Claude palette)
RESET = '\033[0m'
ORANGE = '\033[38;5;209m'
CREAM = '\033[1;38;5;222m'
GOLD = '\033[38;5;180m'
LABEL = '\033[38;5;137m'
DIM = '\033[38;5;240m'
BAR_EMPTY = '\033[38;5;238m'
PERC = '\033[38;5;222m'
GREEN = '\033[38;5;114m'
YELLOW = '\033[38;5;179m'
RED = '\033[38;5;167m'

FILL = '\u2588'
EMPTY = '\u2591'
NBSP = '\u00a0'
SEP = f'{NBSP}{DIM}\u2502{RESET} '
ANSI_RE = re.compile(r'\033\[[0-9;]*m')

USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
CACHE_TTL_SECONDS = 60
PROMPT_CACHE_TTL = 300  # 5-minute prompt cache TTL
CACHE_LOW_THRESHOLD = 120  # 2 minutes — switch from green to yellow
DEBUG_LOG_MAX_BYTES = 1_000_000
TAIL_CHUNK = 16384
