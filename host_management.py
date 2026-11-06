"""Explicit per-IP removal; never executes commands on a remote client."""
import asyncio
import json
import re
import shlex
from ipaddress import ip_address
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field
import database
from config import config
from network_utils import get_local_ip_addresses

router = APIRouter(prefix='/api/hosts')


def address(value):
    try:
        return str(ip_address(value))
    except ValueError:
        raise HTTPException(422, 'Bitte eine gültige Quell-IP verwenden.') from None


def uninstall_command(server):
    servers = list(dict.fromkeys([server, *get_local_ip_addresses()]))
    servers = [value for value in servers if value and re.fullmatch(r'[A-Za-z0-9_.:-]+',value) and value not in ('localhost','127.0.0.1','0.0.0.0','::1')]
    if not servers:
        return ''
    options = ' '.join('--server ' + shlex.quote(value) for value in servers)
    ports = list(dict.fromkeys(port for port in (config.SYSLOG_UDP_PORT,config.SYSLOG_TCP_PORT) if port)) or [5514]
    options += ' ' + ' '.join(f'--port {port}' for port in ports)
    return ('RSYSLOG_SUDO=; [ "$(id -u)" -eq 0 ] || RSYSLOG_SUDO=sudo; '
            'curl -fsSL https://raw.githubusercontent.com/Schello805/rsyslog/main/scripts/uninstall-client.sh '
            '| $RSYSLOG_SUDO bash -s -- ' + options)


def removal_info(host, server):
    conn = database.get_db_connection()
    rules = database.get_all_settings().get('alert_rules',[])
    return {'host':host,'display_host':database.get_host_aliases().get(host,host),
            'logs':conn.execute('SELECT COUNT(*) FROM logs WHERE host=?',(host,)).fetchone()[0],
            'incidents':conn.execute('SELECT COUNT(*) FROM incidents WHERE host=?',(host,)).fetchone()[0],
            'host_rules':sum(rule.get('host')==host for rule in rules),
            'ai_cache':conn.execute('SELECT COUNT(*) FROM analysis_cache').fetchone()[0],
            'uninstall_command':uninstall_command(server)}


@router.get('/removal')
async def preview(request: Request, host: str = Query(...,max_length=255)):
    host = address(host)
    return await asyncio.to_thread(removal_info,host,request.url.hostname or '')


class Removal(BaseModel):
    host: str = Field(...,max_length=255)
    confirm_host: str = Field(...,max_length=255)


def remove_host(host):
    conn = database.get_db_connection()
    with conn:
        count = conn.execute('DELETE FROM logs WHERE host=?',(host,)).rowcount
        for table in ('monitored_hosts','host_aliases','incidents','rule_notifications'):
            conn.execute(f'DELETE FROM {table} WHERE host=?',(host,))
        conn.execute('DELETE FROM source_dns WHERE ip=?',(host,))
        conn.execute('DELETE FROM resource_samples WHERE host=?',(host,))
        # A stored analysis may cover several hosts; this reset is explicit in the dialog.
        conn.execute('DELETE FROM analysis_cache')
        rules_row = conn.execute("SELECT value FROM settings WHERE key='alert_rules'").fetchone()
        if rules_row:
            rules = json.loads(rules_row['value'])
            rules = [rule for rule in rules if rule.get('host') != host]
            conn.execute("UPDATE settings SET value=? WHERE key='alert_rules'",(json.dumps(rules,ensure_ascii=False),))
        names_row = conn.execute("SELECT value FROM settings WHERE key='syslog_source_names'").fetchone()
        if names_row:
            try:
                names = json.loads(names_row['value'])
                if isinstance(names,dict) and host in names:
                    names.pop(host)
                    conn.execute("UPDATE settings SET value=? WHERE key='syslog_source_names'",(json.dumps(names),))
            except (ValueError,TypeError):
                pass
    return count


@router.post('/remove')
async def remove(body: Removal, request: Request):
    origin = request.headers.get('origin')
    if ((origin and origin.rstrip('/') != str(request.base_url).rstrip('/'))
            or request.headers.get('sec-fetch-site') == 'cross-site'
            or request.headers.get('x-rsyslog-action') != 'remove-host'):
        raise HTTPException(403,'Geräte bitte über den Entfernen-Dialog der App löschen.')
    host = address(body.host)
    if body.confirm_host != host:
        raise HTTPException(422,'Zur Bestätigung die vollständige Quell-IP eingeben.')
    from ai_analysis import _busy, _snapshots
    if _busy.locked():
        raise HTTPException(409,'Eine KI-Analyse läuft noch. Bitte danach erneut entfernen.')
    async with _busy:
        count = await asyncio.to_thread(remove_host,host)
        _snapshots.clear()
    from metrics_cache import invalidate
    invalidate()
    from notification_gate import forget_host
    forget_host(host)
    from alerter import forget_host as forget_alert_history
    forget_alert_history(host)
    from host_resolution import forget_source
    forget_source(host)
    return {'success':True,'host':host,'deleted_logs':count}
