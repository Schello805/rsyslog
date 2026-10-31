"""Exercise updater filesystem swaps and rollback with simulated Debian commands."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class UpdaterTests(unittest.TestCase):
    def run_update(self, fail, reuse=False, current=False, missing_syslog=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'app'
            data = root / 'data'
            backup = root / 'backups'
            commands = root / 'commands'
            for path in (app / '.venv/bin', data, backup, commands):
                path.mkdir(parents=True, exist_ok=True)
            (app / 'old-marker').write_text('old application')
            (app / 'REVISION').write_text('abc1234')
            (app / 'requirements.txt').write_text('' if reuse else 'old dependencies')
            (data / 'syslog.db').write_text('original database')
            progress_dir = root / 'data-update'; progress_dir.mkdir()
            config = root / 'config.env'
            config.write_text('original configuration')
            # The old Python validates settings. Fresh Python simulates pip and health.
            old_python = app / '.venv/bin/python'
            old_python.write_text('#!/bin/bash\nif [[ $1 == - ]]; then cat >/dev/null; if [[ ${FAIL_HEALTH:-0} == 1 && -f $(dirname "$0")/../../new-marker ]]; then exit 1; fi; fi\nexit 0\n')
            old_python.chmod(0o755)
            stubs = {
                'systemctl': '#!/bin/sh\nexit 0\n',
                'rsyslogd': '#!/bin/sh\nexit 0\n',
                'git': '''#!/bin/bash
if [[ $1 == clone ]]; then
    target=${@: -1}
    mkdir -p "$target"
    echo new > "$target/new-marker"
    touch "$target/requirements.txt"
elif [[ $3 == rev-parse ]]; then
    if [[ $4 == FETCH_HEAD ]]; then echo newhash
    elif [[ $4 == HEAD ]]; then
        if [[ ${ALREADY_CURRENT:-0} == 1 ]]; then echo newhash; else echo oldhash; fi
    else echo abc1234
    fi
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
            if missing_syslog:
                del stubs['rsyslogd']
                stubs['apt-get'] = '#!/bin/sh\nif [ "$1" = install ]; then printf "#!/bin/sh\\nexit 0\\n" > "$(dirname "$0")/rsyslogd"; chmod +x "$(dirname "$0")/rsyslogd"; fi\n'
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
            environment = dict(os.environ, PATH=f'{commands}:/usr/bin:/bin', FAIL_HEALTH=str(int(fail)), ALREADY_CURRENT=str(int(current)),RSYSLOG_WEB_UPDATE='1')
            result = subprocess.run(['bash', str(isolated)], env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode != 0, fail, result.stdout + result.stderr)
            self.assertEqual((progress_dir/'progress').read_text().strip(), 'rollback' if fail else 'complete')
            if missing_syslog:
                self.assertTrue((commands / 'rsyslogd').exists())
                self.assertIn('Installiere fehlenden rsyslog-Dienst', result.stdout)
            self.assertEqual((data / 'syslog.db').read_text(), 'original database')
            self.assertEqual(config.read_text(), 'original configuration')
            self.assertEqual((app / 'old-marker').exists(), fail or current)
            self.assertEqual((app / 'new-marker').exists(), not fail and not current)
            snapshots = list(backup.iterdir())
            self.assertEqual(len(snapshots), 0 if current else 1)
            if current:
                self.assertIn('Bereits aktuell', result.stdout)
                return
            self.assertEqual('wiederverwenden' in result.stdout, reuse)
            self.assertTrue((snapshots[0] / 'data/syslog.db').exists())

    def test_progress_failure_does_not_interrupt_update_or_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'progress.tmp').mkdir()
            script=Path('scripts/update-debian.sh').read_text()
            function=script.split('progress() {',1)[1].split('# Prevent concurrent updates.',1)[0]
            function='progress() {'+function
            function=function.replace('/var/lib/rsyslog-web-update',str(root))
            result=subprocess.run(['bash','-c','set -euo pipefail\n'+function+'\nprogress rollback\nprintf survived'],
                env=dict(os.environ,RSYSLOG_WEB_UPDATE='1'),capture_output=True,text=True)
            self.assertEqual(result.returncode,0)
            self.assertIn('survived',result.stdout)

    def test_unchanged_dependencies_reuse_environment(self):
        self.run_update(False, reuse=True)

    def test_fast_update_failure_restores_backup(self):
        self.run_update(True, reuse=True)

    def test_current_revision_skips_restart(self):
        self.run_update(False, current=True)

    def test_missing_rsyslog_is_installed_even_without_app_update(self):
        self.run_update(False, current=True, missing_syslog=True)

    def test_installer_migrates_legacy_configuration_and_database(self):
        import sqlite3
        import sys
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'app'
            app.mkdir()
            (app / '.env').write_text('WEB_PORT=8080\nDATABASE_PATH=syslog.db\nNTFY_TOPIC=my-topic\n')
            with sqlite3.connect(app / 'syslog.db') as connection:
                connection.execute('CREATE TABLE logs (message TEXT)')
                connection.execute("INSERT INTO logs VALUES ('preserved')")
            script = Path('scripts/install-debian.sh').read_text().split("<<'PYTHON'\n", 1)[1].split('\nPYTHON', 1)[0]
            script = script.replace('/var/lib/rsyslog-web/syslog.db', str(root / 'installed.db'))
            script = script.replace('/etc/rsyslog-web.env', str(root / 'installed.env'))
            result = subprocess.run([sys.executable, '-', str(app)], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = dotenv_values(root / 'installed.env')
            self.assertEqual(values['WEB_PORT'], '8080')
            self.assertEqual(values['NTFY_TOPIC'], 'my-topic')
            self.assertEqual(values['DATABASE_PATH'], str(root / 'installed.db'))
            with sqlite3.connect(root / 'installed.db') as connection:
                self.assertEqual(connection.execute('SELECT message FROM logs').fetchone()[0], 'preserved')

    def test_successful_update_keeps_backup(self):
        self.run_update(False)

    def test_failed_health_check_restores_previous_app(self):
        self.run_update(True)


if __name__ == '__main__':
    unittest.main()
