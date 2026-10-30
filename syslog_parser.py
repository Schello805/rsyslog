"""
Syslog Parser supporting RFC 3164 (BSD) and RFC 5424 (IETF),
with fallback for unstructured/raw messages.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict

# Standard RFC Syslog Severities
SEVERITY_NAMES = {
    0: "CRITICAL",  # Emergency (System is unusable)
    1: "CRITICAL",  # Alert (Action must be taken immediately)
    2: "CRITICAL",  # Critical conditions
    3: "ERROR",     # Error conditions
    4: "WARN",      # Warning conditions
    5: "NOTICE",    # Normal but significant condition
    6: "INFO",      # Informational messages
    7: "DEBUG",     # Debug-level messages
}

FACILITY_NAMES = {
    0: "kern",
    1: "user",
    2: "mail",
    3: "daemon",
    4: "auth",
    5: "syslog",
    6: "lpr",
    7: "news",
    8: "uucp",
    9: "cron",
    10: "authpriv",
    11: "ftp",
    12: "ntp",
    13: "security",
    14: "console",
    15: "solaris-cron",
    16: "local0",
    17: "local1",
    18: "local2",
    19: "local3",
    20: "local4",
    21: "local5",
    22: "local6",
    23: "local7",
}

# Regex for PRI header: <PRIVAL>
PRI_REGEX = re.compile(r"^<(\d{1,3})>")

# Regex for RFC 5424: <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [STRUCTURED-DATA] MSG
RFC5424_REGEX = re.compile(
    r"^<(?P<pri>\d{1,3})>1\s+"
    r"(?P<timestamp>\S+)\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<appname>\S+)\s+"
    r"(?P<procid>\S+)\s+"
    r"(?P<msgid>\S+)\s+"
    r"(?P<rest>.*)$",
    re.DOTALL,
)

# Regex for RFC 3164: <PRI>Mmm dd hh:mm:ss HOSTNAME TAG[PID]: MSG
RFC3164_REGEX = re.compile(
    r"^<(?P<pri>\d{1,3})>"
    r"(?P<timestamp>[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<tag>[a-zA-Z0-9_\-\.\/]+)(?:\[(?P<pid>\d+)\])?:\s*"
    r"(?P<message>.*)$",
    re.DOTALL,
)


def _parse_syslog_message(raw_data: str, client_ip: str = "unknown") -> Dict[str, Any]:
    """
    Parses a raw syslog packet string into a structured dictionary.
    Guarantees non-empty host, app, level, and message.
    """
    data = raw_data.strip()
    current_time_iso = datetime.now(timezone.utc).isoformat()

    # 1. Try RFC 5424
    m5424 = RFC5424_REGEX.match(data)
    if m5424:
        pri = int(m5424.group("pri"))
        facility_num = pri // 8
        severity_num = pri % 8

        ts_str = m5424.group("timestamp")
        timestamp = ts_str if ts_str != "-" else current_time_iso

        host = m5424.group("hostname")
        if host == "-" or not host:
            host = client_ip

        app = m5424.group("appname")
        if app == "-" or not app:
            app = "syslog"

        rest = m5424.group("rest").strip()
        # Handle structured data if present: [exampleSDID@32473 ...] MSG
        message = rest
        if rest.startswith("- "):
            message = rest[2:].lstrip()
        elif rest.startswith("["):
            # Multiple adjacent SD elements and escaped closing brackets are valid.
            structured = re.match(r'^(?:\[(?:\\.|[^\]\\])*\])+(?:\s+|$)', rest)
            if structured:
                message = rest[structured.end():].strip() or rest

        return {
            "timestamp": timestamp,
            "host": host,
            "app": app,
            "level": SEVERITY_NAMES.get(severity_num, "INFO"),
            "facility": FACILITY_NAMES.get(facility_num, "user"),
            "message": message or "(empty message)",
            "raw": data,
        }

    # 2. Try RFC 3164
    m3164 = RFC3164_REGEX.match(data)
    if m3164:
        pri = int(m3164.group("pri"))
        facility_num = pri // 8
        severity_num = pri % 8

        host = m3164.group("hostname") or client_ip
        app = m3164.group("tag") or "syslog"
        message = m3164.group("message")

        return {
            "timestamp": current_time_iso,
            "host": host,
            "app": app,
            "level": SEVERITY_NAMES.get(severity_num, "INFO"),
            "facility": FACILITY_NAMES.get(facility_num, "user"),
            "message": message or "(empty message)",
            "raw": data,
        }

    # 3. Message has PRI header <PRI> but non-standard body
    pri_match = PRI_REGEX.match(data)
    if pri_match:
        pri = int(pri_match.group(1))
        facility_num = pri // 8
        severity_num = pri % 8
        body = data[pri_match.end() :].strip()

        # Check if starts with tag: message
        parts = body.split(":", 1)
        if len(parts) == 2 and len(parts[0].split()) <= 2:
            tag_candidate = parts[0].strip().split()[-1]
            return {
                "timestamp": current_time_iso,
                "host": client_ip,
                "app": tag_candidate,
                "level": SEVERITY_NAMES.get(severity_num, "INFO"),
                "facility": FACILITY_NAMES.get(facility_num, "user"),
                "message": parts[1].strip(),
                "raw": data,
            }

        return {
            "timestamp": current_time_iso,
            "host": client_ip,
            "app": "syslog",
            "level": SEVERITY_NAMES.get(severity_num, "INFO"),
            "facility": FACILITY_NAMES.get(facility_num, "user"),
            "message": body or data,
            "raw": data,
        }

    # 4. Fallback: unstructured raw string
    # Heuristic: inspect if text contains ERROR, CRIT, WARN
    upper_data = data.upper()
    level = "INFO"
    if "CRIT" in upper_data or "EMERG" in upper_data or "FATAL" in upper_data:
        level = "CRITICAL"
    elif "ERR" in upper_data:
        level = "ERROR"
    elif "WARN" in upper_data:
        level = "WARN"

    return {
        "timestamp": current_time_iso,
        "host": client_ip,
        "app": "generic",
        "level": level,
        "facility": "user",
        "message": data,
        "raw": data,
    }


def parse_syslog_message(raw_data: str, client_ip: str = "unknown") -> Dict[str, Any]:
    from cef_parser import enrich_unifi
    return enrich_unifi(_parse_syslog_message(raw_data, client_ip))
