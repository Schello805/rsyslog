"""
Version and Revision Management for RSyslog Web.
Automatically detects git commit SHA or tag for release info.
"""

import os
import subprocess
from functools import lru_cache

VERSION = "1.0.0"


@lru_cache(maxsize=1)
def get_git_revision() -> str:
    """Returns short git revision or fallback string."""
    # 1. Check environment variable override
    env_rev = os.getenv("APP_REVISION")
    if env_rev:
        return env_rev.strip()

    # 2. Try git rev-parse
    try:
        cmd = ["git", "rev-parse", "--short", "HEAD"]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=1.5)
        rev = output.decode("utf-8").strip()
        if rev:
            return rev
    except Exception:
        pass

    # 3. Try reading from a release file if packaged
    rev_file = os.path.join(os.path.dirname(__file__), "REVISION")
    if os.path.exists(rev_file):
        try:
            with open(rev_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass

    return "initial"


def get_version_info() -> dict:
    """Returns structured version and revision information."""
    rev = get_git_revision()
    return {
        "version": VERSION,
        "revision": rev,
        "display": f"v{VERSION} (rev. {rev})",
    }
