"""
Application configuration for RSyslog Web.
Loads settings from .env file or environment variables.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)


class Config:
    # Web Dashboard Settings
    WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT: int = int(os.getenv("WEB_PORT", "8000"))

    # Syslog Server Settings
    SYSLOG_BIND_HOST: str = os.getenv("SYSLOG_BIND_HOST", "0.0.0.0")
    SYSLOG_UDP_PORT: int = int(os.getenv("SYSLOG_UDP_PORT", "5514"))
    SYSLOG_TCP_PORT: int = int(os.getenv("SYSLOG_TCP_PORT", "5514"))

    # SQLite Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(BASE_DIR / "syslog.db"))

    # Telegram Alerting
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # Ntfy Alerting
    NTFY_TOPIC: str = os.getenv("NTFY_TOPIC", "").strip()
    NTFY_SERVER: str = os.getenv("NTFY_SERVER", "https://ntfy.sh").strip()

    # Alert Thresholds
    ALERT_MIN_LEVEL: str = os.getenv("ALERT_MIN_LEVEL", "ERROR").upper()
    ALERT_THROTTLE_SECONDS: int = int(os.getenv("ALERT_THROTTLE_SECONDS", "10"))

    # Attribution & Links
    GITHUB_URL: str = os.getenv(
        "GITHUB_URL", "https://github.com/Schello805/rsyslog"
    )
    AUTHOR_NAME: str = "Michael Schellenberger"


config = Config()
