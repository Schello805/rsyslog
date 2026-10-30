#!/usr/bin/env python3
"""Root-owned systemd helper. Executes only the fixed installed updater."""
import json
import os
from pathlib import Path
import subprocess
import time

CONTROL = Path('/var/lib/rsyslog-web-update')


def status(state, message):
    temporary = CONTROL / 'status.tmp'
    try:
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        boot_id = None
    temporary.write_text(json.dumps({'state': state, 'message': message, 'updated_at': time.time(), 'boot_id': boot_id}))
    temporary.chmod(0o644)
    os.replace(temporary, CONTROL / 'status.json')


def main():
    status('running', 'Sicherung und Update werden ausgeführt. Die Webapp startet dabei neu.')
    (CONTROL / 'request/start').unlink(missing_ok=True)
    try:
        with (CONTROL / 'update.log').open('w') as output:
            os.chmod(CONTROL / 'update.log', 0o600)
            result = subprocess.run(['/bin/bash', '/opt/rsyslog-web/scripts/update-debian.sh'],
                                    stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            status('failed', 'Update fehlgeschlagen; Wiederherstellung wurde versucht. Details im Server-Log /var/lib/rsyslog-web-update/update.log.')
            return 1
        status('succeeded', 'Update erfolgreich. Seite neu laden.')
        return 0
    except Exception:
        status('failed', 'Update konnte nicht abgeschlossen werden. Dienststatus auf dem Server prüfen.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
