"""
Automated Verification Suite for RSyslog Web.
Validates Database, Parser, Settings, Alert Thresholds, and Versioning.
"""

import os
import sys

def run_tests():
    print("🧪 Running Verification Tests...")

    # 1. Test Version module
    import version
    v_info = version.get_version_info()
    assert "version" in v_info
    assert "revision" in v_info
    print(f"✅ Version module ok: {v_info['display']}")

    # 2. Test Parser
    from syslog_parser import parse_syslog_message
    
    # RFC 3164
    p1 = parse_syslog_message("<34>Oct 11 22:14:15 srv01 myapp[123]: Disk full on /var", client_ip="192.168.1.10")
    assert p1["host"] == "srv01"
    assert p1["app"] == "myapp"
    assert p1["level"] == "CRITICAL"
    assert "Disk full" in p1["message"]
    print("✅ RFC 3164 parsing passed")

    # RFC 5424
    p2 = parse_syslog_message("<134>1 2026-10-30T18:00:00Z myhost myapp 1234 ID42 - System rebooting", client_ip="10.0.0.1")
    assert p2["host"] == "myhost"
    assert p2["app"] == "myapp"
    assert p2["level"] == "INFO"
    assert "System rebooting" in p2["message"]
    print("✅ RFC 5424 parsing passed")

    # 3. Test Database & Settings
    import database
    database.init_db()

    # Settings test
    database.save_all_settings({
        "alert_threshold_count": 2,
        "alert_min_level": "WARN",
        "ntfy_topic": "test-topic-123",
    })
    s = database.get_all_settings()
    assert s["alert_threshold_count"] == 2
    assert s["alert_min_level"] == "WARN"
    assert s["ntfy_topic"] == "test-topic-123"
    print("✅ Database Settings persistent key-value passed")

    # 4. Test Alert threshold evaluation
    import alerter
    fp = "test-host:nginx:ERROR"
    # First incident: count is 1
    cnt1 = alerter.record_incident_and_check_threshold(fp, threshold_count=2, window_minutes=5)
    assert cnt1 == 1
    # Second incident: count is 2 (reaches threshold of 2!)
    cnt2 = alerter.record_incident_and_check_threshold(fp, threshold_count=2, window_minutes=5)
    assert cnt2 == 2
    print("✅ Frequency threshold counting passed (1 -> 2 triggers)")

    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
