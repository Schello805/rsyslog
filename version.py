"""Version and automatically increasing Git revision for the dashboard."""
import os
from pathlib import Path
import subprocess

VERSION = "1.0.0"
BASE_DIR = Path(__file__).resolve().parent


def git_value(*arguments):
    return subprocess.check_output(
        ['git', '-C', str(BASE_DIR), *arguments],
        stderr=subprocess.DEVNULL, timeout=1.5,
    ).decode().strip()


def release_value(filename, default):
    try:
        return (BASE_DIR / filename).read_text(encoding='utf-8').strip() or default
    except OSError:
        return default


def get_git_revision() -> str:
    override = os.getenv('APP_REVISION', '').strip()
    if override:
        return override
    try:
        return git_value('rev-parse', '--short', 'HEAD')
    except (OSError, subprocess.SubprocessError):
        return release_value('REVISION', 'initial')


def get_revision_number() -> int:
    try:
        return int(git_value('rev-list', '--count', 'HEAD'))
    except (OSError, subprocess.SubprocessError, ValueError):
        try:
            return int(release_value('REVISION_NUMBER', '0'))
        except ValueError:
            return 0


def get_version_info() -> dict:
    revision = get_git_revision()
    number = get_revision_number()
    return {
        'version': VERSION,
        'revision': revision,
        'revision_number': number,
        'display': f'v{VERSION} · Rev. {number} ({revision})',
    }
