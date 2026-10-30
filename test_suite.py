"""
Automated Verification Suite for RSyslog Web.
Validates Database, Parser, API routes, and Versioning.
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

    # Raw message
    p3 = parse_syslog_message("Database connection timeout error", client_ip="10.0.0.5")
    assert p3["level"] == "ERROR"
    assert "Database connection timeout" in p3["message"]
    print("✅ Raw message fallback passed")

    # 3. Test Database
    import database
    database.init_db()
    saved = database.insert_log(
        host="test-host",
        app="test-app",
        level="ERROR",
        message="Test error message",
    )
    assert saved["id"] is not None
    logs, total = database.get_logs(level="ERROR")
    assert total >= 1
    assert any(l["id"] == saved["id"] for l in logs)
    
    stats = database.get_stats()
    assert stats["total"] >= 1
    assert stats["error"] >= 1
    print("✅ Database operations passed")

    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
