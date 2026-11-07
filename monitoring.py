"""Heartbeat-based host status and periodic retention cleanup."""
import asyncio
import logging
import time
import database

logger = logging.getLogger(__name__)


async def observe_log(log):
    if log.get('app') == 'resource-monitor' and log.get('message') == 'HEARTBEAT':
        await asyncio.to_thread(database.touch_monitored_host, log['host'])


async def maintenance():
    last_cleanup = 0
    last_storage_warning = float('-inf')
    # Let HTTP readiness and the initial dashboard load finish before disk-heavy
    # retention/storage maintenance competes for the same SQLite database.
    await asyncio.sleep(30)
    while True:
        # Status/DNS failures must not skip retention or storage enforcement.
        try:
            await asyncio.to_thread(database.mark_offline_hosts)
        except Exception:
            logger.exception('Host status update failed')
        try:
            if last_cleanup == 0 or time.monotonic() - last_cleanup >= 3600:
                settings = await asyncio.to_thread(database.get_all_settings)
                await asyncio.to_thread(database.delete_logs_older_than, settings['retention_days'])
                last_cleanup = time.monotonic()
        except Exception:
            logger.exception('Retention cleanup failed')
        try:
            from storage_policy import trim_database
            await asyncio.to_thread(trim_database)
        except Exception:
            logger.exception('Size cleanup failed')
        try:
            from analysis_cache import cleanup as cleanup_analyses
            await asyncio.to_thread(lambda: cleanup_analyses(database.get_db_connection()))
            from storage_policy import trim_database, status
            storage = await asyncio.to_thread(status)
            if storage['warning'] and time.monotonic() - last_storage_warning >= 3600:
                from alerter import process_alert
                entry = await asyncio.to_thread(database.insert_log, host='rsyslog', app='storage-monitor',
                    level='WARN', message=storage['message'])
                await process_alert(entry)
                last_storage_warning = time.monotonic()
            from host_resolution import refresh_known_sources
            await asyncio.to_thread(refresh_known_sources)
        except Exception:
            logger.exception('Background maintenance failed')
        await asyncio.sleep(30)
