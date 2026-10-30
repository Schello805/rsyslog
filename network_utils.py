"""
Network utilities for RSyslog Web.
Detects server LAN IP addresses and generates dynamic remote connection links.
"""

import socket
import subprocess
from typing import Any, Dict, List
from config import config


def get_local_ip_addresses() -> List[str]:
    """Detects available non-loopback IPv4 addresses on the host machine."""
    ips = set()

    # 1. macOS ipconfig probe
    for iface in ["en0", "en1", "en2", "eth0", "wlan0"]:
        try:
            out = subprocess.check_output(
                ["ipconfig", "getifaddr", iface],
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            ).decode().strip()
            if out and not out.startswith("127."):
                ips.add(out)
        except Exception:
            pass

    # 2. Linux hostname -I
    try:
        out = subprocess.check_output(
            ["hostname", "-I"],
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        ).decode().strip()
        for candidate in out.split():
            if candidate and not candidate.startswith("127."):
                ips.add(candidate)
    except Exception:
        pass

    # 3. Standard socket probe
    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    # 4. Outbound socket dummy connect
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        sock_ip = s.getsockname()[0]
        s.close()
        if sock_ip and not sock_ip.startswith("127."):
            ips.add(sock_ip)
    except Exception:
        pass

    sorted_ips = sorted(list(ips))
    return sorted_ips if sorted_ips else ["127.0.0.1"]


def get_network_connection_info(request_host: str = "") -> Dict[str, Any]:
    """
    Returns structured connection metadata for remote syslog senders.
    Prioritizes request_host if it is an actual LAN IP or domain.
    """
    available_ips = get_local_ip_addresses()
    
    # Preferred primary IP
    primary_ip = available_ips[0] if available_ips else "127.0.0.1"
    if request_host and request_host.split(":")[0] not in ("localhost", "127.0.0.1", "0.0.0.0"):
        primary_ip = request_host.split(":")[0]

    return {
        "primary_ip": primary_ip,
        "available_ips": available_ips,
        "syslog_udp_port": config.SYSLOG_UDP_PORT,
        "syslog_tcp_port": config.SYSLOG_TCP_PORT,
        "web_port": config.WEB_PORT,
        "webhook_url": f"http://{primary_ip}:{config.WEB_PORT}/api/logs",
        "udp_target": f"{primary_ip}:{config.SYSLOG_UDP_PORT}",
        "tcp_target": f"{primary_ip}:{config.SYSLOG_TCP_PORT}",
    }
