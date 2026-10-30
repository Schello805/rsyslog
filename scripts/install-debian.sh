#!/usr/bin/env bash
# Debian 12+ / Ubuntu with systemd. Run as root.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Bitte als root ausführen.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'systemd erforderlich'; exit 1; }
app=/opt/rsyslog-web
repo=https://github.com/Schello805/rsyslog.git
ref=${RSYSLOG_REF:-main}
apt-get update
apt-get install -y python3 python3-venv git ca-certificates
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git clone --branch "$ref" "$repo" "$work/source"
[[ ! -e $app ]] || { echo "$app existiert bereits; Installation abgebrochen, Daten unverändert." >&2; exit 1; }
id rsyslog-web >/dev/null 2>&1 || useradd --system --home-dir /var/lib/rsyslog-web --shell /usr/sbin/nologin rsyslog-web
install -d -o rsyslog-web -g rsyslog-web -m 0750 /var/lib/rsyslog-web
install -d -m 0755 "$app"
cp -a "$work/source/." "$app/"
git -C "$app" rev-parse --short HEAD > "$app/REVISION"
git -C "$app" rev-list --count HEAD > "$app/REVISION_NUMBER"
python3 -m venv "$app/.venv"
"$app/.venv/bin/pip" install -r "$app/requirements.txt"
if [[ ! -e /etc/rsyslog-web.env ]]; then
    cat > /etc/rsyslog-web.env <<'ENV'
WEB_HOST=0.0.0.0
WEB_PORT=8000
SYSLOG_BIND_HOST=0.0.0.0
SYSLOG_UDP_PORT=5514
SYSLOG_TCP_PORT=5514
DATABASE_PATH=/var/lib/rsyslog-web/syslog.db
ENV
    chmod 0640 /etc/rsyslog-web.env
    chown root:rsyslog-web /etc/rsyslog-web.env
fi
cat > /etc/systemd/system/rsyslog-web.service <<'UNIT'
[Unit]
Description=RSyslog Web
Wants=network-online.target
After=network-online.target

[Service]
User=rsyslog-web
Group=rsyslog-web
WorkingDirectory=/opt/rsyslog-web
EnvironmentFile=/etc/rsyslog-web.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/rsyslog-web/.venv/bin/python /opt/rsyslog-web/main.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/rsyslog-web

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now rsyslog-web
sleep 2
systemctl is-active --quiet rsyslog-web || { journalctl -u rsyslog-web -n 30 --no-pager; exit 1; }
echo 'Installiert. Dashboard: http://SERVER-IP:8000 | Syslog UDP/TCP: 5514'
echo 'Konfiguration: /etc/rsyslog-web.env | Daten: /var/lib/rsyslog-web'
