"""Exercise updater filesystem swaps and rollback with simulated Debian commands."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class UpdaterTests(unittest.TestCase):
    def run_update(self, fail):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'app'
            data = root / 'data'
            backup = root / 'backups'
            commands = root / 'commands'
            for path in (app / '.venv/bin', data, backup, commands):
                path.mkdir(parents=True, exist_ok=True)
            (app / 'old-marker').write_text('old application')
            (data / 'syslog.db').write_text('original database')
            config = root / 'config.env'
            config.write_text('original configuration')
            # The old Python validates settings. Fresh Python simulates pip and health.
            old_python = app / '.venv/bin/python'
            old_python.write_text('#!/bin/sh\ncat >/dev/null\nexit 0\n')
            old_python.chmod(0o755)
            stubs = {
                'systemctl': '#!/bin/sh\nexit 0\n',
                'git': '''#!/bin/bash
if [[ $1 == clone ]]; then
    target=${@: -1}
    mkdir -p "$target"
    echo new > "$target/new-marker"
    touch "$target/requirements.txt"
elif [[ $3 == rev-parse ]]; then echo abc1234
else echo 7
fi
''',
                'python3': '''#!/bin/bash
if [[ $2 == venv ]]; then
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'STUB'
#!/bin/bash
if [[ $1 == - ]]; then
    cat >/dev/null
    if [[ ${FAIL_HEALTH:-0} == 1 ]]; then exit 1; fi
fi
exit 0
STUB
    chmod +x "$3/bin/python"
fi
''',
            }
            for name, body in stubs.items():
                path = commands / name
                path.write_text(body)
                path.chmod(0o755)
            script = Path('scripts/update-debian.sh').read_text()
            # Keep the real update/backup/rollback logic; isolate only host paths and prerequisites.
            script = script.replace('[[ $EUID == 0 ]]', '[[ 1 == 1 ]]')
            script = script.replace('[[ -d /run/systemd/system ]]', '[[ 1 == 1 ]]')
            script = script.replace('/opt/rsyslog-web', str(app))
            script = script.replace('/etc/rsyslog-web.env', str(config))
            script = script.replace('/var/lib/rsyslog-web', str(data))
            script = script.replace('/var/backups/rsyslog-web-', str(backup / 'rsyslog-web-'))
            script = script.replace('/run/lock/rsyslog-web-update.lock', str(root / 'update.lock'))
            script = script.replace('flock -n 9', 'true')
            isolated = root / 'update.sh'
            isolated.write_text(script)
            environment = dict(os.environ, PATH=f'{commands}:/usr/bin:/bin', FAIL_HEALTH=str(int(fail)))
            result = subprocess.run(['bash', str(isolated)], env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode != 0, fail, result.stdout + result.stderr)
            self.assertEqual((data / 'syslog.db').read_text(), 'original database')
            self.assertEqual(config.read_text(), 'original configuration')
            self.assertEqual((app / 'old-marker').exists(), fail)
            self.assertEqual((app / 'new-marker').exists(), not fail)
            snapshots = list(backup.iterdir())
            self.assertEqual(len(snapshots), 1)
            self.assertTrue((snapshots[0] / 'data/syslog.db').exists())

    def test_successful_update_keeps_backup(self):
        self.run_update(False)

    def test_failed_health_check_restores_previous_app(self):
        self.run_update(True)


if __name__ == '__main__':
    unittest.main()
