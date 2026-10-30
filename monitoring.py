"""Heartbeat-based host status and periodic retention cleanup."""
import asyncio
import logging
import time
import database

logger = logging.getLogger(__name__)


async def status_event(host, recovered):
    from alerter import process_alert
    from syslog_server import broadcast_log
    entry = await asyncio.to_thread(database.insert_log, host=host, app='host-monitor',
        level='WARN' if recovered else 'ERROR',
        message='ENTWARNUNG: Heartbeat wieder empfangen' if recovered else 'ALARM: Seit 5 Minuten kein Heartbeat')
    await process_alert(entry)
    await broadcast_log(entry)


async def observe_log(log):
    if log.get('app') == 'resource-monitor' and log.get('message') == 'HEARTBEAT':
        recovered = await asyncio.to_thread(database.touch_monitored_host, log['host'])
        if recovered:
            await status_event(log['host'], True)


async def maintenance():
    last_cleanup = 0
    while True:
        try:
            for host in await asyncio.to_thread(database.mark_offline_hosts):
                await status_event(host, False)
            if time.monotonic() - last_cleanup >= 3600:
                settings = await asyncio.to_thread(database.get_all_settings)
                await asyncio.to_thread(database.delete_logs_older_than, settings['retention_days'])
                last_cleanup = time.monotonic()
        except Exception:
            logger.exception('Background maintenance failed')
        await asyncio.sleep(30)
