#!/usr/bin/env bash
# Update installations created by install-debian.sh; restore on failure.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Bitte als root ausführen.' >&2; exit 1; }
app=/opt/rsyslog-web
[[ -x $app/.venv/bin/python && -f /etc/rsyslog-web.env ]] || { echo 'Keine unterstützte Debian-Installation gefunden.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'systemd erforderlich'; exit 1; }
# Progress is a fixed, root-owned file; only the web helper enables reporting.
progress() {
    if [[ ${RSYSLOG_WEB_UPDATE:-0} == 1 && -d /var/lib/rsyslog-web-update ]]; then
        printf '%s\n' "$1" > /var/lib/rsyslog-web-update/progress.tmp || return 0
        chmod 0644 /var/lib/rsyslog-web-update/progress.tmp || return 0
        mv /var/lib/rsyslog-web-update/progress.tmp /var/lib/rsyslog-web-update/progress || return 0
    fi
}
# Prevent concurrent updates.
exec 9>/run/lock/rsyslog-web-update.lock
flock -n 9 || { echo 'Ein Update läuft bereits.' >&2; exit 1; }
progress checking
# Repair older installations as well, even when the app is already current.
if ! command -v rsyslogd >/dev/null 2>&1; then
    echo 'Installiere fehlenden rsyslog-Dienst …'
    apt-get update
    apt-get install -y rsyslog
fi
rsyslogd -N1
systemctl enable --now rsyslog
ref=${RSYSLOG_REF:-main}
# Keep large staging copies off /tmp, which may be a RAM-backed filesystem.
work=$(mktemp -d "${app%/*}/.rsyslog-web-update.XXXXXX")
echo "Update-Vorbereitung: $work"
backup=''
changed=0
was_active=0
cleanup() {
    result=$?
    trap - EXIT
    if ((result != 0 && changed)); then
        progress rollback
        echo "Update fehlgeschlagen. Stelle Sicherung $backup wieder her." >&2
        systemctl stop rsyslog-web || true
        if [[ -d $backup/app ]]; then
            rm -rf "$app"
            mv "$backup/app" "$app"
        fi
        if [[ -d $backup/data ]]; then
            rm -rf /var/lib/rsyslog-web
            cp -a "$backup/data" /var/lib/rsyslog-web
        fi
        if ((was_active)); then systemctl start rsyslog-web || echo 'Dienst konnte nicht wieder gestartet werden.' >&2; fi
    fi
    rm -rf "$work"
    exit "$result"
}
trap cleanup EXIT
# Refuse custom database paths: they require their own backup strategy.
"$app/.venv/bin/python" - <<'PY'
from dotenv import dotenv_values
from pathlib import Path
settings = dotenv_values('/etc/rsyslog-web.env')
if Path(settings.get('DATABASE_PATH', '')).as_posix() != '/var/lib/rsyslog-web/syslog.db':
    raise SystemExit('Update abgebrochen: eigener DATABASE_PATH. Datenbank separat sichern und manuell aktualisieren.')
PY
progress downloading
echo 'Prüfe GitHub auf neue Änderungen …'
git -C "$app" fetch --no-tags https://github.com/Schello805/rsyslog.git "$ref"
target=$(git -C "$app" rev-parse FETCH_HEAD)
current=$(git -C "$app" rev-parse HEAD)
target_short=$(git -C "$app" rev-parse --short "$target")
installed=$(cat "$app/REVISION" 2>/dev/null || true)
if [[ $target == "$current" && $installed == "$target_short" ]]; then
    if [[ -f $app/scripts/setup-web-updates.sh ]]; then bash "$app/scripts/setup-web-updates.sh"; fi
    progress complete
    echo 'Bereits aktuell. Kein Neustart erforderlich.'
    exit 0
fi
progress preparing
# Copy existing objects locally; only fetch transfers new Git data.
git clone --local --no-hardlinks "$app" "$work/source"
git -C "$work/source" checkout -B "$ref" "$target"
git -C "$work/source" remote set-url origin https://github.com/Schello805/rsyslog.git
git -C "$work/source" rev-parse --short HEAD > "$work/source/REVISION"
git -C "$work/source" rev-list --count HEAD > "$work/source/REVISION_NUMBER"
reuse_environment=0
if cmp -s "$app/requirements.txt" "$work/source/requirements.txt" && "$app/.venv/bin/python" -m pip check; then
    echo 'Abhängigkeiten unverändert: vorhandene Python-Umgebung wiederverwenden.'
    reuse_environment=1
    # Copy before stopping; the original stays in the rollback backup.
    echo 'Kopiere Python-Umgebung in das Vorbereitungsverzeichnis …'
    cp -a "$app/.venv" "$work/source/.venv"
    echo 'Python-Umgebung vorbereitet.'
else
    echo 'Abhängigkeiten geändert: Pakete vorbereiten …'
    python3 -m venv "$work/check-venv"
    "$work/check-venv/bin/python" -m pip download -r "$work/source/requirements.txt" -d "$work/packages"
fi
echo 'Prüfe Python-Dateien …'
"$app/.venv/bin/python" -m compileall -q -x '/(\.venv|\.git)/' "$work/source"
progress backup
echo 'Erstelle Sicherungsverzeichnis …'
backup=$(mktemp -d /var/backups/rsyslog-web-$(date +%Y%m%d-%H%M%S).XXXXXX)
chmod 0700 "$backup"
cp -a /etc/rsyslog-web.env "$backup/config.env"
systemctl is-active --quiet rsyslog-web && was_active=1
echo 'Stoppe Webapp für eine konsistente Datenbanksicherung …'
systemctl stop rsyslog-web
echo 'Sichere Datenbank …'
# The stopped database, including WAL files, is copied consistently.
if ! cp -a /var/lib/rsyslog-web "$backup/data"; then
    ((was_active == 0)) || systemctl start rsyslog-web
    exit 1
fi
progress installing
echo 'Wechsle auf die vorbereitete Revision …'
changed=1
mv "$app" "$backup/app"
mv "$work/source" "$app"
if ((reuse_environment == 0)); then
    python3 -m venv "$app/.venv"
    "$app/.venv/bin/python" -m pip install --no-index --find-links "$work/packages" -r "$app/requirements.txt"
fi
if [[ -f $app/scripts/setup-web-updates.sh ]]; then bash "$app/scripts/setup-web-updates.sh"; fi
progress restarting
echo 'Starte Webapp und prüfe die neue Revision …'
systemctl start rsyslog-web
progress verifying
"$app/.venv/bin/python" - <<'PY'
import json
from pathlib import Path
import time
import urllib.request
from dotenv import dotenv_values
settings = dotenv_values('/etc/rsyslog-web.env')
host = settings.get('WEB_HOST', '0.0.0.0')
host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
if ':' in host:
    host = f'[{host}]'
url = f"http://{host}:{int(settings.get('WEB_PORT', '8000'))}/api/version"
expected = Path('/opt/rsyslog-web/REVISION').read_text().strip()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(30):
    try:
        with opener.open(url, timeout=2) as response:
            version = json.load(response)
        if version['revision'] == expected:
            print('Update erfolgreich:', version['display'])
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(1)
else:
    raise SystemExit('Gesundheitsprüfung fehlgeschlagen')
PY
changed=0
progress complete
echo "Sicherung der bisherigen Installation: $backup"
