#!/usr/bin/env python3
"""
Test Log Sender for RSyslog Web.
Allows sending RFC 3164, RFC 5424, and HTTP Webhook test logs with various levels.
"""

import argparse
import json
import socket
import sys
import time
import urllib.request


def send_udp_syslog(message: str, host: str = "127.0.0.1", port: int = 5514) -> None:
    """Sends a raw syslog message via UDP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message.encode("utf-8"), (host, port))
        print(f"✅ [UDP -> {host}:{port}] Gesendet: {message}")
    except Exception as e:
        print(f"❌ Fehler beim UDP-Senden: {e}")
    finally:
        sock.close()


def send_http_log(payload: dict, url: str = "http://127.0.0.1:8000/api/logs") -> None:
    """Sends a structured log via HTTP Webhook POST."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"✅ [HTTP Webhook -> {resp.status}] Gesendet: {payload}")
    except Exception as e:
        print(f"❌ Fehler beim HTTP Webhook: {e}")


def send_demo_scenario(udp_port: int = 5514, web_port: int = 8000) -> None:
    """Sends a diverse batch of realistic logs to demonstrate the dashboard."""
    print("🚀 Sende eine bunte Reihe an realistischen Test-Logs...\n")

    logs = [
        # 1. Info via RFC3164 UDP
        ("<14>Oct 30 18:00:01 gateway01 systemd[1]: Started Daily apt upgrade and clean activities.", "udp"),
        # 2. Warning via RFC5424 UDP
        ("<132>1 2026-10-30T18:01:10.123Z srv-db01 postgresql 5122 - - High disk I/O latency observed on /var/lib/postgresql/data (94ms)", "udp"),
        # 3. Info via HTTP Webhook
        ({
            "host": "frontend-edge01",
            "app": "nginx",
            "level": "INFO",
            "message": "GET /api/v1/status HTTP/1.1 200 452ms user-agent='UptimeRobot'",
        }, "http"),
        # 4. Error via RFC3164 UDP
        ("<11>Oct 30 18:02:15 auth-server sshd[9841]: Failed password for invalid user admin from 192.168.1.150 port 44211 ssh2", "udp"),
        # 5. Critical via HTTP Webhook (Triggers Alert!)
        ({
            "host": "prod-node-03",
            "app": "kubernetes",
            "level": "CRITICAL",
            "message": "Out of Memory (OOMKilled): Container 'order-service' killed by Linux kernel.",
        }, "http"),
        # 6. Warn via HTTP
        ({
            "host": "srv-storage",
            "app": "zfs",
            "level": "WARN",
            "message": "Pool 'tank' usage exceeded 85% capacity threshold.",
        }, "http"),
    ]

    for item, method in logs:
        if method == "udp":
            send_udp_syslog(item, port=udp_port)
        else:
            send_http_log(item, url=f"http://127.0.0.1:{web_port}/api/logs")
        time.sleep(0.3)

    print("\n🎉 Alle Test-Logs erfolgreich übermittelt! Öffne das Dashboard im Browser.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RSyslog Web Test Log Sender")
    parser.add_argument("--udp-port", type=int, default=5514, help="Syslog UDP Port (Standard: 5514)")
    parser.add_argument("--web-port", type=int, default=8000, help="Web Dashboard Port (Standard: 8000)")
    parser.add_argument(
        "--level",
        choices=["INFO", "WARN", "ERROR", "CRITICAL"],
        default=None,
        help="Einzelsendung mit bestimmtem Level",
    )
    parser.add_argument("--message", type=str, default=None, help="Nachrichtentext")
    parser.add_argument("--host", type=str, default="test-client.lan", help="Hostname")
    parser.add_argument("--app", type=str, default="demo-app", help="App-Name")

    args = parser.parse_args()

    if args.message or args.level:
        level = args.level or "INFO"
        msg = args.message or f"Test-Nachricht mit Level {level}"
        send_http_log(
            {"host": args.host, "app": args.app, "level": level, "message": msg},
            url=f"http://127.0.0.1:{args.web_port}/api/logs",
        )
    else:
        send_demo_scenario(udp_port=args.udp_port, web_port=args.web_port)
