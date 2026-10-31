#!/usr/bin/env bash
# Called by the root installer/updater. Web process only writes a fixed request file.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Bitte als root ausführen.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -d -m 0755 /usr/local/lib/rsyslog-web /var/lib/rsyslog-web-update
install -d -o root -g rsyslog-web -m 0770 /var/lib/rsyslog-web-update/request
install -m 0755 "$source_dir/web-update-runner.py" /usr/local/lib/rsyslog-web/web-update-runner.py
cat > /etc/systemd/system/rsyslog-web-update.path <<'UNIT'
[Unit]
Description=Watch for authorized RSyslog Web update requests

[Path]
PathExists=/var/lib/rsyslog-web-update/request/start
Unit=rsyslog-web-update.service

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/rsyslog-web-update.service <<'UNIT'
[Unit]
Description=Update RSyslog Web with backup and rollback
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/rsyslog-web/web-update-runner.py
ExecStopPost=/usr/bin/python3 /usr/local/lib/rsyslog-web/web-update-runner.py --finalize
TimeoutStartSec=infinity
UNIT
install -d -m 0755 /etc/systemd/system/rsyslog-web.service.d
cat > /etc/systemd/system/rsyslog-web.service.d/updates.conf <<'UNIT'
[Service]
ReadWritePaths=/var/lib/rsyslog-web-update/request
UNIT
systemctl daemon-reload
systemctl enable --now rsyslog-web-update.path
