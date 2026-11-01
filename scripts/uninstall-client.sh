#!/usr/bin/env bash
# Removes only this project's monitor and matching forwarding lines in its known config files.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Bitte als root ausführen.' >&2; exit 1; }
servers=()
ports=()
while (($#)); do
    case "$1" in
        --server) [[ $# -ge 2 ]] || exit 2; servers+=("$2"); shift 2 ;;
        --port) [[ $# -ge 2 ]] || exit 2; ports+=("$2"); shift 2 ;;
        *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
    esac
done
((${#servers[@]})) || { echo 'Syslog-Ziel angeben: --server IP --port 5514' >&2; exit 2; }
((${#ports[@]})) || ports=(5514)
for port in "${ports[@]}"; do
    [[ $port =~ ^[0-9]+$ ]] && ((port > 0 && port <= 65535)) || exit 2
done
for server in "${servers[@]}"; do
    [[ $server =~ ^[a-zA-Z0-9][a-zA-Z0-9.:-]*$ ]] || { echo 'Ungültiger Server' >&2; exit 2; }
done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
forward_re='^[[:space:]]*\*\.\*[[:space:]]+@@?([^[:space:]]+):([0-9]+)[[:space:]]*$'
for name in 99-resource-monitor-remote.conf 99-remote.conf; do
    file=/etc/rsyslog.d/$name
    [[ -f $file && ! -L $file ]] || continue
    cp -p "$file" "$work/$name"
    changed=0
    : > "$work/$name.new"
    while IFS= read -r line || [[ -n $line ]]; do
        remove=0
        if [[ $line =~ $forward_re ]]; then
            target=${BASH_REMATCH[1]}
            target_port=${BASH_REMATCH[2]}
            for server in "${servers[@]}"; do
                for port in "${ports[@]}"; do
                    if [[ $target_port == "$port" && ( $target == "$server" || $target == "[$server]" ) ]]; then remove=1; fi
                done
            done
        fi
        if ((remove)); then changed=1; else printf '%s\n' "$line" >> "$work/$name.new"; fi
    done < "$file"
    if ((changed)); then
        cp "$work/$name.new" "$file"
        [[ -s $file ]] || rm -f "$file"
        echo "Weiterleitung entfernt: $file"
    fi
done
if command -v rsyslogd >/dev/null && ! rsyslogd -N1; then
    for name in 99-resource-monitor-remote.conf 99-remote.conf; do
        [[ ! -f $work/$name ]] || cp -p "$work/$name" "/etc/rsyslog.d/$name"
    done
    echo 'Konfiguration ungültig: Weiterleitungsdateien wiederhergestellt.' >&2
    exit 1
fi
if command -v systemctl >/dev/null; then
    for unit in rsyslog-resource-monitor.timer rsyslog-resource-monitor.service; do
        if systemctl cat "$unit" >/dev/null 2>&1; then systemctl disable --now "$unit"; fi
    done
fi
rm -f /etc/systemd/system/rsyslog-resource-monitor.timer /etc/systemd/system/rsyslog-resource-monitor.service
rm -f /usr/local/sbin/rsyslog-resource-monitor.py /etc/rsyslog-resource-monitor.json
rm -f /var/lib/rsyslog-resource-monitor/state.json /var/lib/rsyslog-resource-monitor/state.tmp
rmdir /var/lib/rsyslog-resource-monitor 2>/dev/null || true
if command -v systemctl >/dev/null; then
    systemctl daemon-reload
    if systemctl is-active --quiet rsyslog.service; then systemctl restart rsyslog.service; fi
fi
echo 'Client-Monitor entfernt. Lokales rsyslog und andere Weiterleitungsregeln bleiben erhalten.'
echo 'Bei weiteren eigenen Weiterleitungsdateien oder Netzwerkgeräten das Syslog-Ziel zusätzlich dort entfernen.'
