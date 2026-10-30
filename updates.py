"""Cached GitHub update checks and a fixed systemd request, never shell commands from HTTP."""
import asyncio
import json
from pathlib import Path
import re
import secrets
import time
import httpx
from fastapi import HTTPException
from version import get_version_info

CONTROL = Path('/var/lib/rsyslog-web-update')
KEY_FILE = Path('/etc/rsyslog-web-update.key')
API = 'https://api.github.com/repos/Schello805/rsyslog'
_cache = {}
_check_lock = asyncio.Lock()
_start_lock = asyncio.Lock()


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
    if (CONTROL / 'request/start').exists() and job.get('state') != 'running':
        job = {'state': 'queued'}
    return job


def is_supported():
    return KEY_FILE.is_file() and (CONTROL / 'request').is_dir()


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


async def request_update(key):
    if not is_supported():
        raise HTTPException(503, 'Web-Updates sind auf diesem Server noch nicht eingerichtet')
    try:
        expected = KEY_FILE.read_text().strip()
    except OSError:
        raise HTTPException(503, 'Update-Schlüssel ist nicht lesbar; Servereinrichtung prüfen')
    if not expected or not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(403, 'Ungültiger Update-Schlüssel')
    async with _start_lock:
        if job_status().get('state') in ('running', 'queued'):
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
