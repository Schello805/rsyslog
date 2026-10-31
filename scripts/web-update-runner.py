#!/usr/bin/env python3
"""Root-owned systemd helper. Executes only the fixed installed updater."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CONTROL = Path('/var/lib/rsyslog-web-update')
_started_at = None


def status(state, message):
    temporary = CONTROL / 'status.tmp'
    try:
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        boot_id = None
    try:
        process_start = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        process_start = None
    temporary.write_text(json.dumps({'state': state, 'message': message, 'updated_at': time.time(),
                                     'started_at': _started_at, 'boot_id': boot_id,
                                     'pid': os.getpid(), 'process_start': process_start}))
    temporary.chmod(0o644)
    os.replace(temporary, CONTROL / 'status.json')


def main():
    global _started_at
    _started_at = time.time()
    (CONTROL / 'progress').unlink(missing_ok=True)
    status('running', 'Sicherung und Update werden ausgeführt. Die Webapp startet dabei neu.')
    (CONTROL / 'request/start').unlink(missing_ok=True)
    try:
        with (CONTROL / 'update.log').open('w') as output:
            os.chmod(CONTROL / 'update.log', 0o600)
            result = subprocess.run(['/bin/bash', '/opt/rsyslog-web/scripts/update-debian.sh'],
                                    stdout=output, stderr=subprocess.STDOUT,
                                    env=dict(os.environ, RSYSLOG_WEB_UPDATE='1'))
        if result.returncode:
            status('failed', 'Update fehlgeschlagen; Wiederherstellung wurde versucht. Details im Server-Log /var/lib/rsyslog-web-update/update.log.')
            return 1
        status('succeeded', 'Update erfolgreich. Seite neu laden.')
        return 0
    except Exception:
        status('failed', 'Update konnte nicht abgeschlossen werden. Dienststatus auf dem Server prüfen.')
        return 1


def finalize():
    """systemd calls this even when the main helper was killed unexpectedly."""
    try:
        job = json.loads((CONTROL / 'status.json').read_text())
    except (OSError, ValueError):
        return 0
    if job.get('state') == 'running':
        status('failed', 'Update-Dienst wurde unerwartet beendet. Details in /var/lib/rsyslog-web-update/update.log und im systemd-Journal prüfen.')
    return 0


if __name__ == '__main__':
    raise SystemExit(finalize() if sys.argv[1:] == ['--finalize'] else main())
