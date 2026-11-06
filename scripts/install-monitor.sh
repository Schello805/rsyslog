#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Bitte als root ausführen.' >&2; exit 1; }
non_interactive=0
forward_only=0
while (($#)); do
    case "$1" in
        --non-interactive) non_interactive=1 ;;
        --forward-only) forward_only=1 ;;
        *) echo "Unbekannte Option: $1" >&2; exit 1 ;;
    esac
    shift
done
command -v systemctl >/dev/null
[[ -d /run/systemd/system ]] || { echo 'systemd muss im Gast laufen.' >&2; exit 1; }
apt-get update
if ((forward_only)); then
    apt-get install -y rsyslog util-linux
else
    apt-get install -y python3 rsyslog curl util-linux
fi
# Noninteractive inputs are supplied by the PVE host through environment variables.
ask() {
    local answer
    if ((non_interactive)); then REPLY=$2; return; fi
    read -r -p "$1 [$2]: " answer </dev/tty
    REPLY=${answer:-$2}
}
ask 'Syslog-Server (IP/Hostname)' "${RSYSLOG_SERVER:-192.168.1.163}"; server=$REPLY
[[ $server =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'Ungültiger Server'; exit 1; }
ask 'Port' "${RSYSLOG_PORT:-5514}"; port=$REPLY
[[ $port =~ ^[0-9]+$ ]] && ((port > 0 && port <= 65535)) || exit 1
if ((forward_only)); then
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
else
ask 'CPU-Alarmschwelle (%)' '90'; cpu=$REPLY
ask 'RAM-Alarmschwelle (%)' '90'; ram=$REPLY
ask 'Speicherplatz-Alarmschwelle (%)' '90'; disk=$REPLY
ask 'CPU/RAM Mindestdauer (Sekunden)' '600'; duration=$REPLY
ask 'Warnung wiederholen nach Sekunden' '1800'; repeat=$REPLY
ask 'Dateisystempfade durch Komma getrennt' '/'; mounts=$REPLY
ask 'Messbereich auto/host/cgroup' 'auto'; scope=$REPLY
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
source_dir=''
# bash reading stdin (curl | bash) has no BASH_SOURCE entry under set -u.
if [[ -n ${BASH_SOURCE[0]:-} ]]; then
    source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || source_dir=''
fi
for file in resource-monitor.py rsyslog-resource-monitor.service rsyslog-resource-monitor.timer; do
    if [[ -n $source_dir && -f $source_dir/$file ]]; then
        cp "$source_dir/$file" "$work/$file"
    else
        curl -fsSL "https://raw.githubusercontent.com/Schello805/rsyslog/main/scripts/$file" -o "$work/$file"
    fi
done
python3 - "$cpu" "$ram" "$disk" "$duration" "$repeat" "$mounts" "$scope" "$work/config.json" <<'PY'
import json, sys
from pathlib import Path
cpu, ram, disk = map(float, sys.argv[1:4])
duration, repeat = map(int, sys.argv[4:6])
mounts = [x.strip() for x in sys.argv[6].split(',') if x.strip()]
scope = sys.argv[7]
assert all(0 < x <= 100 for x in (cpu,ram,disk)), 'Schwellen müssen zwischen 0 und 100 liegen'
assert duration >= 0 and repeat > 0
assert scope in ('auto','host','cgroup')
assert mounts and all(Path(x).is_absolute() and Path(x).is_dir() for x in mounts), 'Ungültiger Dateisystempfad'
Path(sys.argv[8]).write_text(json.dumps(dict(cpu=cpu,ram=ram,disk=disk,duration=duration,repeat=repeat,mount=mounts,scope=scope)))
PY
python3 "$work/resource-monitor.py" --config "$work/config.json" --dry-run
install -m 0755 "$work/resource-monitor.py" /usr/local/sbin/rsyslog-resource-monitor.py
install -m 0644 "$work/config.json" /etc/rsyslog-resource-monitor.json
install -m 0644 "$work/rsyslog-resource-monitor.service" /etc/systemd/system/
install -m 0644 "$work/rsyslog-resource-monitor.timer" /etc/systemd/system/
fi
printf '*.* @%s:%s\n' "$server" "$port" > "$work/remote.conf"
# Keep other forwarding rules intact; backup this installer's previous rule.
[[ ! -f /etc/rsyslog.d/99-resource-monitor-remote.conf ]] || cp -p /etc/rsyslog.d/99-resource-monitor-remote.conf /etc/rsyslog.d/99-resource-monitor-remote.conf.bak
install -m 0644 "$work/remote.conf" /etc/rsyslog.d/99-resource-monitor-remote.conf
if ! rsyslogd -N1; then
    rm /etc/rsyslog.d/99-resource-monitor-remote.conf
    [[ ! -f /etc/rsyslog.d/99-resource-monitor-remote.conf.bak ]] || mv /etc/rsyslog.d/99-resource-monitor-remote.conf.bak /etc/rsyslog.d/99-resource-monitor-remote.conf
    exit 1
fi
systemctl enable --now rsyslog
systemctl restart rsyslog
if ((forward_only == 0)); then
    systemctl daemon-reload
    systemctl enable --now rsyslog-resource-monitor.timer
    systemctl start rsyslog-resource-monitor.service
fi
echo 'Syslog eingerichtet. Bestehende Weiterleitungen zum gleichen Ziel vermeiden (sonst doppelte Logs).'
