"""Cached GitHub update checks and a fixed systemd request, never shell commands from HTTP."""
import asyncio
import json
from pathlib import Path
import re
import subprocess
import time
import httpx
from fastapi import HTTPException
from version import get_version_info

CONTROL = Path('/var/lib/rsyslog-web-update')
API = 'https://api.github.com/repos/Schello805/rsyslog'
_cache = {}
_check_lock = asyncio.Lock()
_start_lock = asyncio.Lock()
_service_cache = (0, None)


def _service_state():
    """Read the fixed helper unit for old status files without a process identity."""
    global _service_cache
    now = time.monotonic()
    if now - _service_cache[0] < 5:
        return _service_cache[1]
    try:
        result = subprocess.run(['systemctl', 'show', 'rsyslog-web-update.service', '--property=ActiveState', '--value'],
                                capture_output=True, text=True, timeout=2, check=False)
        state = result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        state = None
    _service_cache = (now, state)
    return state



def job_status():
    try:
        job = json.loads((CONTROL / 'status.json').read_text())
    except (OSError, ValueError):
        job = {'state': 'idle'}
    if job.get('state') == 'running' and job.get('boot_id'):
        try:
            if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != job['boot_id']:
                job = {'state': 'failed', 'message': 'Server während des Updates neu gestartet; Dienststatus prüfen'}
        except OSError:
            pass
    if job.get('state') == 'running':
        if job.get('pid') and job.get('process_start') and Path('/proc').is_dir():
            try:
                process_start = Path(f"/proc/{int(job['pid'])}/stat").read_text().rsplit(')', 1)[1].split()[19]
                alive = process_start == str(job['process_start'])
            except (FileNotFoundError, ProcessLookupError):
                alive = False
            except (OSError, ValueError, IndexError):
                alive = None
            if alive is False:
                job = dict(job, state='failed', message='Update-Prozess wurde beendet, ohne ein Ergebnis zu melden. Update-Protokoll auf dem Server prüfen.')
        else:
            service_state = _service_state()
            if service_state in ('inactive', 'failed'):
                job = dict(job, state='failed', message='Update-Dienst läuft nicht mehr. Der alte Laufstatus wurde nicht abgeschlossen. Update-Protokoll auf dem Server prüfen.')
            elif service_state not in ('active', 'activating', 'reloading') and time.time() - job.get('updated_at', time.time()) > 900:
                job = dict(job, state='unknown', message='Alter Update-Status ohne überprüfbaren Prozess. systemctl status rsyslog-web-update und das Update-Protokoll auf dem Server prüfen.')
    if (CONTROL / 'request/start').exists() and job.get('state') != 'running':
        job = {'state': 'queued'}
    if job.get('state') in ('running', 'failed'):
        phases = {'checking':('Voraussetzungen prüfen',1),'downloading':('Herunterladen',2),
                  'preparing':('Vorbereiten',3),'backup':('Sichern',4),'installing':('Installieren',5),
                  'restarting':('Neu starten',6),'verifying':('Neue Revision prüfen',7),
                  'complete':('Abschließen',7),'rollback':('Sicherung wiederherstellen',None)}
        try:
            phase = (CONTROL / 'progress').read_text().strip()
        except OSError:
            phase = ''
        if phase in phases:
            job = dict(job,phase=phase,phase_label=phases[phase][0],step=phases[phase][1],total_steps=7)
    return job


def is_supported():
    return (CONTROL / 'request').is_dir()


async def check_updates():
    current = get_version_info()
    key = current['revision']
    async with _check_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            result = dict(cached[1])
        else:
            result = {'available': False, 'check_state': 'unknown'}
            try:
                if not re.fullmatch(r'[0-9a-f]{7,40}', key):
                    raise ValueError('Installierte Git-Revision ist nicht bekannt')
                async with httpx.AsyncClient(timeout=10, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'RSyslog-Web'}) as client:
                    response = await client.get(f'{API}/commits/main')
                    response.raise_for_status()
                    latest = response.json()['sha']
                    if not re.fullmatch(r'[0-9a-f]{40}', latest):
                        raise ValueError('Ungültige GitHub-Antwort')
                    if latest.startswith(key):
                        result = {'available': False, 'check_state': 'current', 'latest_revision': latest}
                    else:
                        comparison = await client.get(f'{API}/compare/{key}...{latest}')
                        comparison.raise_for_status()
                        comparison = comparison.json()
                        ahead = comparison.get('status') == 'ahead' and int(comparison.get('ahead_by', 0)) > 0
                        number = current['revision_number'] + int(comparison.get('ahead_by', 0))
                        result = {'available': ahead, 'check_state': 'available' if ahead else 'manual',
                                  'latest_revision': latest, 'latest_version': '.'.join(current['version'].split('.')[:2] + [str(number)]) if ahead else None}
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                result = {'available': False, 'check_state': 'unknown', 'message': 'GitHub derzeit nicht erreichbar oder Revision nicht vergleichbar'}
            _cache.clear()
            _cache[key] = (time.monotonic(), dict(result))
    return dict(result, current=current, can_start=is_supported(), job=job_status())


async def request_update():
    if not is_supported():
        raise HTTPException(503, 'Web-Updates sind auf diesem Server noch nicht eingerichtet')
    async with _start_lock:
        if job_status().get('state') in ('running', 'queued', 'unknown'):
            raise HTTPException(409, 'Ein Update läuft bereits')
        info = await check_updates()
        if not info['available']:
            raise HTTPException(409, 'Kein automatisch installierbares Update verfügbar')
        try:
            # Atomic fixed filename; the root-owned helper ignores all file contents.
            with (CONTROL / 'request/start').open('x'):
                pass
        except FileExistsError:
            raise HTTPException(409, 'Ein Update wurde bereits angefordert')
        except OSError:
            raise HTTPException(503, 'Update-Dienst nicht beschreibbar; Servereinrichtung prüfen')
    return {'state': 'queued'}
