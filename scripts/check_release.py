"""
Validate that all version sources agree and changelog is release-ready.
"""

# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <version>')
        sys.exit(2)

    tag = sys.argv[1]
    year = datetime.now(UTC).year
    errors: list[str] = []

    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    init = (ROOT / 'src' / 'claude_vibeline' / '__init__.py').read_text(encoding='utf-8')
    license_text = (ROOT / 'LICENSE').read_text(encoding='utf-8')

    # Unreleased section must be empty
    unreleased_match = re.search(r'## \[Unreleased\]\s*\n(.*?)(?=\n## \[)', changelog, re.DOTALL)
    if unreleased_match and unreleased_match.group(1).strip():
        errors.append(f'Unreleased section is not empty:\n{unreleased_match.group(1).strip()}')

    # Latest version in changelog must match tag
    version_match = re.search(r'## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})', changelog)
    if not version_match:
        errors.append('No versioned release found in CHANGELOG.md')
    else:
        changelog_version = version_match.group(1)
        changelog_date = version_match.group(2)

        if changelog_version != tag:
            errors.append(f'Changelog version ({changelog_version}) does not match tag ({tag})')

        today = datetime.now().astimezone().strftime('%Y-%m-%d')
        if changelog_date != today:
            errors.append(f'Changelog date ({changelog_date}) is not today ({today})')

    # pyproject.toml version must match tag
    pyproject_match = re.search(r'^version = "(.+)"', pyproject, re.MULTILINE)
    pyproject_version = pyproject_match.group(1) if pyproject_match else None
    if pyproject_version != tag:
        errors.append(f'pyproject.toml version ({pyproject_version}) does not match tag ({tag})')

    # __init__.py version must match tag
    init_match = re.search(r"__version__ = '(.+)'", init)
    init_version = init_match.group(1) if init_match else None
    if init_version != tag:
        errors.append(f'__init__.py version ({init_version}) does not match tag ({tag})')

    # Copyright year must include current year
    copyright_match = re.search(r'Copyright \(c\) (.+?) ', license_text)
    if copyright_match:
        copyright_years = copyright_match.group(1)
        if str(year) not in copyright_years:
            errors.append(f'LICENSE copyright ({copyright_years}) does not include current year ({year})')
    else:
        errors.append('No copyright line found in LICENSE')

    if errors:
        for error in errors:
            print(f'ERROR: {error}')
        sys.exit(1)

    print(f'All checks passed for {tag}')


if __name__ == '__main__':
    main()
