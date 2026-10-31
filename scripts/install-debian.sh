#!/usr/bin/env bash
# Debian 12+ / Ubuntu with systemd. Run as root.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Bitte als root ausführen.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'systemd erforderlich'; exit 1; }
app=/opt/rsyslog-web
repo=https://github.com/Schello805/rsyslog.git
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ -f $source_dir/main.py && -d $source_dir/.git ]] || {
    echo 'Bitte zuerst mit git clone herunterladen und im Projekt bash scripts/install-debian.sh ausführen.' >&2
    exit 1
}
if [[ -e $app && ! -d $app/.git ]]; then
    echo "$app existiert ohne Git-Repository. Installation abgebrochen; Daten bleiben erhalten." >&2
    exit 1
fi
apt-get update
apt-get install -y python3 python3-venv git ca-certificates util-linux rsyslog
rsyslogd -N1
systemctl enable --now rsyslog
if [[ ! -d $app/.git ]]; then
    git clone --no-hardlinks "$source_dir" "$app"
    git -C "$app" remote set-url origin "$repo"
elif [[ $source_dir != "$app" ]]; then
    echo "Vorhandenes Repository verwenden: cd $app && git pull --ff-only && bash scripts/install-debian.sh" >&2
    exit 1
fi
id rsyslog-web >/dev/null 2>&1 || useradd --system --home-dir /var/lib/rsyslog-web --shell /usr/sbin/nologin rsyslog-web
install -d -o rsyslog-web -g rsyslog-web -m 0750 /var/lib/rsyslog-web
git -C "$app" rev-parse --short HEAD > "$app/REVISION"
git -C "$app" rev-list --count HEAD > "$app/REVISION_NUMBER"
python3 -m venv "$app/.venv"
"$app/.venv/bin/python" -m pip install -r "$app/requirements.txt"
if [[ ! -e /etc/rsyslog-web.env ]]; then
    # Preserve configuration and logs from an earlier git/start.sh installation.
    "$app/.venv/bin/python" - "$app" <<'PYTHON'
from pathlib import Path
import sqlite3
import sys
from dotenv import dotenv_values, set_key
app = Path(sys.argv[1])
settings = dotenv_values(app / '.env') if (app / '.env').exists() else {}
original = Path(settings.get('DATABASE_PATH') or app / 'syslog.db')
if not original.is_absolute():
    original = app / original
destination = Path('/var/lib/rsyslog-web/syslog.db')
if original.exists() and not destination.exists():
    with sqlite3.connect(f'file:{original}?mode=ro', uri=True) as source, sqlite3.connect(destination) as target:
        source.backup(target)
config = Path('/etc/rsyslog-web.env')
config.touch(mode=0o640)
for key, value in {'WEB_HOST': '0.0.0.0', 'WEB_PORT': '8000', 'SYSLOG_BIND_HOST': '0.0.0.0',
                   'SYSLOG_UDP_PORT': '5514', 'SYSLOG_TCP_PORT': '5514', **settings,
                   'DATABASE_PATH': str(destination)}.items():
    if value is not None:
        set_key(str(config), key, str(value))
PYTHON
    chmod 0640 /etc/rsyslog-web.env
    chown root:rsyslog-web /etc/rsyslog-web.env
    chown -R rsyslog-web:rsyslog-web /var/lib/rsyslog-web
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
bash "$app/scripts/setup-web-updates.sh"
systemctl daemon-reload
systemctl enable rsyslog-web
systemctl restart rsyslog-web
sleep 2
systemctl is-active --quiet rsyslog-web || { journalctl -u rsyslog-web -n 30 --no-pager; exit 1; }
echo 'Installiert. Dashboard: http://SERVER-IP:8000 | Syslog UDP/TCP: 5514'
echo 'Konfiguration: /etc/rsyslog-web.env | Daten: /var/lib/rsyslog-web'
echo 'Updates: sudo bash /opt/rsyslog-web/scripts/update-debian.sh'
echo 'Web-Updates können im Footer ohne Update-Schlüssel gestartet werden.'
