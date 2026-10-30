#!/usr/bin/env python3
"""Install syslog forwarding on local running PVE guests, optionally resource monitoring."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import tarfile
import time


def run(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f'Exit {result.returncode}').strip())
    return result.stdout


def vm_execute(vmid, script, timeout=900):
    response = json.loads(run(['qm', 'guest', 'exec', str(vmid), '--pass-stdin', '1',
                               '--synchronous', '0', '--', '/bin/bash', '-s'], input=script))
    pid = response['pid']
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = json.loads(run(['qm', 'guest', 'exec-status', str(vmid), str(pid)]))
        if status.get('exited'):
            output = status.get('out-data', '') + status.get('err-data', '')
            if status.get('exitcode', 1) != 0:
                raise RuntimeError(output or f'Gast-Exitcode {status.get("exitcode")}')
            return output
        time.sleep(2)
    raise RuntimeError(f'Zeitlimit: Gastprozess {pid} läuft möglicherweise weiter; nicht ungeprüft erneut starten.')


def payload(server, port, monitor):
    files = ['install-monitor.sh']
    if monitor:
        files += ['resource-monitor.py', 'rsyslog-resource-monitor.service', 'rsyslog-resource-monitor.timer']
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name in files:
            archive.add(Path(__file__).resolve().parent / name, arcname=name)
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    options = '--non-interactive' + ('' if monitor else ' --forward-only')
    return f'''set -euo pipefail
command -v apt-get >/dev/null
[[ -d /run/systemd/system ]] || {{ echo 'systemd fehlt'; exit 1; }}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf '%s' '{encoded}' | base64 -d | tar -xz -C "$work"
export DEBIAN_FRONTEND=noninteractive
export RSYSLOG_SERVER={shlex.quote(server)}
export RSYSLOG_PORT={port}
bash "$work/install-monitor.sh" {options}
logger -p user.notice -t pve-install 'Syslog-Weiterleitung vom PVE-Host eingerichtet'
'''


def ids(value):
    return {int(part) for part in value.split(',') if part.strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, help='RSyslog receiver IPv4 address or hostname')
    parser.add_argument('--port', type=int, default=5514)
    parser.add_argument('--monitor', action='store_true', help='Also install CPU/RAM/disk monitoring with default thresholds')
    parser.add_argument('--only', type=ids, default=set(), help='Comma-separated guest IDs')
    parser.add_argument('--exclude', type=ids, default=set(), help='Comma-separated guest IDs to skip')
    parser.add_argument('--dry-run', action='store_true', help='List guests and prerequisites without changing guests')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', args.server) or not 0 < args.port <= 65535:
        parser.error('Ungültige Serveradresse oder Port')
    if os.geteuid() != 0:
        parser.error('Auf dem PVE-Host als root ausführen')
    node = socket.gethostname().split('.')[0]
    guests = []
    for kind in ('lxc', 'qemu'):
        guests += [dict(guest, kind=kind) for guest in json.loads(run(
            ['pvesh', 'get', f'/nodes/{node}/{kind}', '--output-format', 'json']))]
    missing = args.only - {int(guest['vmid']) for guest in guests}
    if missing:
        parser.error(f'IDs nicht auf diesem Host gefunden: {sorted(missing)}')
    script = payload(args.server, args.port, args.monitor)
    results = []
    for guest in sorted(guests, key=lambda guest: int(guest['vmid'])):
        vmid = int(guest['vmid'])
        label = f"{guest['kind']} {vmid} ({guest.get('name', '?')})"
        if (args.only and vmid not in args.only) or vmid in args.exclude:
            results.append((label, 'ÜBERSPRUNGEN', 'nicht ausgewählt'))
            continue
        if guest.get('status') != 'running' or guest.get('template'):
            results.append((label, 'ÜBERSPRUNGEN', 'gestoppt oder Vorlage'))
            continue
        try:
            if guest['kind'] == 'qemu':
                info = json.loads(run(['qm', 'guest', 'cmd', str(vmid), 'get-osinfo']))
                if info.get('id') not in ('debian', 'ubuntu') and info.get('distro') not in ('debian', 'ubuntu'):
                    results.append((label, 'ÜBERSPRUNGEN', 'nur Debian/Ubuntu-VMs unterstützt'))
                    continue
            else:
                run(['pct', 'exec', str(vmid), '--', 'sh', '-c',
                     'command -v apt-get >/dev/null && test -d /run/systemd/system'])
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
            results.append((label, 'ÜBERSPRUNGEN', f'Voraussetzungen fehlen / Guest Agent nicht erreichbar: {error}'))
            continue
        if args.dry_run:
            results.append((label, 'BEREIT', 'würde installiert'))
            continue
        print(f'Installiere auf {label} …', flush=True)
        try:
            if guest['kind'] == 'lxc':
                result = subprocess.run(['pct', 'exec', str(vmid), '--', '/bin/bash', '-s'],
                                        input=script, text=True, capture_output=True, timeout=900)
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                output = result.stdout
            else:
                output = vm_execute(vmid, script)
            print(output, flush=True)
            results.append((label, 'OK', 'installiert'))
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
            results.append((label, 'FEHLER', str(error)))
    print('\nErgebnis:')
    for label, status, detail in results:
        print(f'{status}: {label}: {detail}')
    # Skips must also be visible to unattended callers as incomplete installation.
    return int(any(status in ('FEHLER', 'ÜBERSPRUNGEN') and detail != 'nicht ausgewählt'
                   for _, status, detail in results))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        raise SystemExit(str(error))
