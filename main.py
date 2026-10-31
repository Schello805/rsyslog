"""
Main FastAPI application for RSyslog Web.
Integrates Web UI, REST API, WebSockets, and asynchronous Syslog listeners.
"""

import asyncio
import csv
import io
import json
import logging
import re
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Literal

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from alerter import (
    process_alert,
    send_test_alert,
    test_ntfy_config,
    test_telegram_config,
)
from config import config
from database import (
    clear_all_logs,
    delete_logs_older_than,
    get_all_settings,
    get_db_connection,
    get_filter_options,
    get_logs,
    get_stats,
    init_db,
    insert_log,
    save_all_settings,
)
from syslog_parser import parse_syslog_message
from syslog_server import (
    broadcast_log,
    register_log_subscriber,
    server_manager,
    unregister_log_subscriber,
)
from version import get_version_info
from network_utils import get_network_connection_info
from system_metrics import collect_system_metrics

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rsyslog.main")
# HTTP access messages forwarded back through local syslog can trigger a live-feed loop.
# Keep application/error logging, but suppress per-request access logs for all launch modes.
logging.getLogger("uvicorn.access").disabled = True

# Each browser has its own bounded queue and sender.
_active_websockets = {}


async def websocket_broadcaster(log_entry):
    payload = json.dumps(log_entry)
    for ws, queue in list(_active_websockets.items()):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            _active_websockets.pop(ws, None)
            # The sender closes this client once its pending send completes/times out.


async def websocket_sender(ws, queue):
    try:
        while ws in _active_websockets:
            payload = await queue.get()
            await asyncio.wait_for(ws.send_text(payload), 3)
    finally:
        _active_websockets.pop(ws, None)
        try:
            await asyncio.wait_for(ws.close(code=1013), 2)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: initializes DB, Syslog server, and listeners."""
    logger.info("Initializing RSyslog Web database...")
    init_db()

    # Optional retention cleanup on startup
    settings = get_all_settings()
    retention_days = int(settings.get("retention_days", 0))
    if retention_days > 0:
        deleted = delete_logs_older_than(retention_days)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} logs older than {retention_days} days.")

    # Register broadcaster for real-time live logs
    register_log_subscriber(websocket_broadcaster)

    # Start UDP & TCP Syslog servers
    logger.info("Starting Syslog background listeners...")
    await server_manager.start()

    logger.info(
        f"RSyslog Web started successfully! Open http://{config.WEB_HOST}:{config.WEB_PORT} in your browser."
    )
    from monitoring import maintenance
    maintenance_task = asyncio.create_task(maintenance())
    try:
        yield
    finally:
        maintenance_task.cancel()
        await asyncio.gather(maintenance_task, return_exceptions=True)

    # Shutdown
    logger.info("Shutting down Syslog listeners...")
    unregister_log_subscriber(websocket_broadcaster)
    await server_manager.stop()
    logger.info("RSyslog Web stopped.")


app = FastAPI(
    title="RSyslog Web",
    description="Zentraler Syslog-Empfänger mit strukturierter Tabellenansicht und Alarmierung.",
    version=get_version_info()["version"],
    lifespan=lifespan,
)


app.mount('/assets', StaticFiles(directory=Path(__file__).parent / 'static'), name='assets')


@app.get('/api/updates')
async def api_updates():
    from updates import check_updates
    return await check_updates()


@app.get('/api/updates/status')
async def api_update_status():
    from updates import job_status
    return job_status()


@app.post('/api/updates/start', status_code=202)
async def api_start_update(request: Request):
    from urllib.parse import urlsplit
    from updates import request_update
    origin = request.headers.get('origin')
    if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.headers.get('host')):
        raise HTTPException(403, 'Updates müssen von derselben Oberfläche gestartet werden')
    if request.headers.get('sec-fetch-site') == 'cross-site' or request.headers.get('x-rsyslog-update') != '1':
        raise HTTPException(403, 'Updates bitte über den Update-Dialog der App starten')
    return await request_update()


class WebhookLogPayload(BaseModel):
    """Schema for direct HTTP Webhook JSON ingestion."""
    level: Optional[str] = Field("INFO", description="DEBUG, INFO, WARN, ERROR, CRITICAL")
    host: Optional[str] = Field(None, description="Sender host/ip")
    app: Optional[str] = Field(None, description="Application or service name")
    facility: Optional[str] = Field("user", description="Syslog facility")
    message: str = Field(..., description="Log message text")
    timestamp: Optional[str] = Field(None, description="ISO timestamp override")


def render_template(template_path):
    """New revisions get fresh local assets even when the browser cached old scripts."""
    revision = get_version_info()['revision']
    content = re.sub(r'((?:src|href)="/assets/[^"?]+)(?:\?[^" ]*)?"',
                     lambda match: f'{match.group(1)}?v={revision}"',
                     template_path.read_text(encoding='utf-8'))
    return HTMLResponse(content=content, headers={'Cache-Control': 'no-store'})


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the single-page dashboard HTML."""
    from pathlib import Path

    template_path = Path(__file__).parent / "templates" / "index.html"
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard template not found")
    return render_template(template_path)


@app.get("/settings", response_class=HTMLResponse)
async def serve_settings():
    """Serves the dedicated Settings HTML page."""
    from pathlib import Path

    template_path = Path(__file__).parent / "templates" / "settings.html"
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Settings template not found")
    return render_template(template_path)


@app.get("/guide", response_class=HTMLResponse)
@app.get("/setup", response_class=HTMLResponse)
@app.get("/connect", response_class=HTMLResponse)
async def serve_guide():
    """Serves the remote setup guide with pre-configured IP and connection parameters."""
    from pathlib import Path

    template_path = Path(__file__).parent / "templates" / "guide.html"
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Guide template not found")
    return render_template(template_path)


@app.get("/api/network-info")
async def api_network_info(request: Request):
    """Returns detected LAN IP addresses and connection ports."""
    req_host = request.headers.get("host", "")
    return get_network_connection_info(req_host)


@app.get("/api/logs")
async def api_get_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    level: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    app: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    hours: Optional[int] = Query(None, ge=1, le=8760),
    sort_by: Literal['timestamp', 'level', 'host', 'app', 'message'] = Query('timestamp'),
    sort_order: Literal['asc', 'desc'] = Query('desc'),
    exclude_host: Optional[List[str]] = Query(None, max_length=200),
):
    """Retrieves paginated and filtered logs."""
    logs, total = get_logs(
        page=page,
        per_page=per_page,
        level=level,
        host=host,
        app=app,
        search=search,
        hours=hours,
        sort_by=sort_by,
        sort_order=sort_order,
        exclude_hosts=exclude_host,
    )
    return {
        "logs": logs,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


class HostNamePayload(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    name: str = Field('', max_length=80)


@app.put('/api/hosts/name')
async def api_set_host_name(payload: HostNamePayload):
    from database import set_host_alias
    if not payload.host.strip() or any(ord(char) < 32 for char in payload.name):
        raise HTTPException(422, 'Bitte einen gültigen Host und einen einzeiligen Anzeigenamen verwenden')
    return set_host_alias(payload.host, payload.name)


@app.get("/api/hosts")
async def api_hosts():
    from database import get_monitored_hosts
    return {"hosts": get_monitored_hosts(), "udp_dropped": server_manager.dropped}


@app.get("/api/dashboard")
async def api_dashboard():
    from database import get_dashboard_summary
    summary = await asyncio.to_thread(get_dashboard_summary)
    summary["udp_dropped"] = server_manager.dropped
    from database import get_ingestion_metrics
    summary["ingestion"] = await asyncio.to_thread(get_ingestion_metrics)
    return summary


@app.get('/api/system')
async def api_system_metrics():
    result = await asyncio.to_thread(collect_system_metrics)
    result['receiver'] = {
        'udp_dropped': server_manager.dropped,
        'queue_size': server_manager.queue.qsize(),
        'queue_capacity': server_manager.queue.maxsize,
        'tcp_connections': server_manager.active_tcp_clients,
        'udp_listening': server_manager.udp_transport is not None,
        'tcp_listening': server_manager.tcp_server is not None,
        'udp_port': config.SYSLOG_UDP_PORT, 'tcp_port': config.SYSLOG_TCP_PORT,
    }
    result['websocket_clients'] = len(_active_websockets)
    return result


@app.get("/api/stats")
async def api_get_stats():
    """Returns dashboard statistics (totals, counts by level, active hosts)."""
    return get_stats()


@app.get("/api/filters")
async def api_get_filters():
    """Returns distinct hosts and applications for UI dropdown filters."""
    return get_filter_options()


@app.post("/api/logs", status_code=201)
async def api_post_log(request: Request):
    """
    HTTP Webhook endpoint for direct log ingestion.
    Supports both JSON payloads and raw text strings.
    """
    content_type = request.headers.get("content-type", "")
    client_ip = request.client.host if request.client else "webhook"

    if "application/json" in content_type:
        try:
            body = await request.json()
            # If payload is structured JSON
            if isinstance(body, dict) and "message" in body:
                saved = insert_log(
                    apply_ingestion_rules=True,
                    host=body.get("host") or client_ip,
                    app=body.get("app") or "webhook",
                    level=body.get("level") or "INFO",
                    facility=body.get("facility") or "user",
                    message=body.get("message") or "",
                    raw=json.dumps(body),
                    timestamp=body.get("timestamp"),
                )
            else:
                # Raw JSON object fallback
                saved = insert_log(
                    apply_ingestion_rules=True,
                    host=client_ip,
                    app="webhook",
                    level="INFO",
                    facility="user",
                    message=json.dumps(body),
                    raw=json.dumps(body),
                )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    else:
        # Plain text payload
        raw_body = (await request.body()).decode("utf-8", errors="replace")
        from host_resolution import identify_source
        parsed = await asyncio.to_thread(identify_source, parse_syslog_message(raw_body, client_ip=client_ip), client_ip)
        saved = insert_log(
            apply_ingestion_rules=True,
            host=parsed["host"],
            app=parsed["app"],
            level=parsed["level"],
            facility=parsed["facility"],
            message=parsed["message"],
            raw=parsed["raw"],
            timestamp=parsed["timestamp"],
            metadata=parsed.get("metadata"),
        )

    if saved is None:
        return JSONResponse(status_code=202, content={"success": True, "discarded": True, "reason": "DEBUG-Meldungen werden verworfen"})

    # Trigger alerts and realtime broadcast
    from monitoring import observe_log
    await observe_log(saved)
    await process_alert(saved)
    await broadcast_log(saved)

    return {"success": True, "log": saved}


@app.post("/api/logs/clear")
async def api_clear_logs():
    """Clears all stored logs from the database."""
    deleted_count = clear_all_logs()
    return {"success": True, "deleted": deleted_count}


@app.get("/api/logs/export")
async def api_export_logs(
    format: str = Query("csv", pattern="^(csv|json)$"),
    level: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    app: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    hours: Optional[int] = Query(None, ge=1, le=8760),
    sort_by: Literal['timestamp', 'level', 'host', 'app', 'message'] = Query('timestamp'),
    sort_order: Literal['asc', 'desc'] = Query('desc'),
    exclude_host: Optional[List[str]] = Query(None, max_length=200),
):
    """Exports matching logs as a downloadable CSV or JSON file."""
    # Retrieve all matching rows (up to 10,000 for export)
    logs, _ = get_logs(
        page=1,
        per_page=10000,
        level=level,
        host=host,
        app=app,
        search=search,
        hours=hours,
        sort_by=sort_by,
        sort_order=sort_order,
        exclude_hosts=exclude_host,
    )

    if format == "json":
        content = json.dumps(logs, indent=2, ensure_ascii=False)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=syslog-export.json"},
        )
    else:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Timestamp", "Level", "Host", "Anzeigename", "App", "Facility", "Message"])
        for row in logs:
            writer.writerow([
                row.get("id"),
                row.get("timestamp"),
                row.get("level"),
                row.get("host"),
                row.get("display_host", row.get("host")),
                row.get("app"),
                row.get("facility"),
                row.get("message"),
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=syslog-export.csv"},
        )


@app.get("/api/version")
async def api_version():
    """Returns application version, revision, and metadata."""
    info = get_version_info()
    info["github_url"] = config.GITHUB_URL
    info["author"] = config.AUTHOR_NAME
    return info


@app.post("/api/test-alert")
async def api_trigger_test_alert():
    """Sends a test alert to configured channels (Telegram / Ntfy)."""
    results = await send_test_alert()
    return {"success": True, "results": results}


@app.get("/api/settings")
async def api_get_settings():
    """Returns all current notification and threshold settings."""
    return get_all_settings()


@app.post("/api/settings")
async def api_save_settings(request: Request):
    """Saves updated notification and threshold settings."""
    try:
        body = await request.json()
        if any(str(key).startswith("ai_") for key in body):
            raise ValueError("KI-Einstellungen bitte über /api/ai/settings speichern.")
        save_all_settings(body)
        return {"success": True, "settings": get_all_settings()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/settings/test-telegram")
async def api_test_telegram(request: Request):
    """Tests Telegram bot credentials provided in payload."""
    body = await request.json()
    token = body.get("token", "").strip()
    chat_id = body.get("chat_id", "").strip()
    res = await test_telegram_config(token, chat_id)
    return res


@app.post("/api/settings/test-ntfy")
async def api_test_ntfy(request: Request):
    """Tests Ntfy configuration provided in payload."""
    body = await request.json()
    topic = body.get("topic", "").strip()
    server = body.get("server", "https://ntfy.sh").strip()
    res = await test_ntfy_config(topic, server)
    return res


@app.post("/api/database/cleanup")
async def api_database_cleanup(request: Request):
    """Deletes logs older than the specified retention days."""
    body = await request.json()
    days = int(body.get("days", 0))
    deleted = delete_logs_older_than(days)
    return {"success": True, "deleted": deleted}


@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time log streaming WebSocket."""
    await websocket.accept()
    queue = asyncio.Queue(maxsize=100)
    _active_websockets[websocket] = queue
    sender = asyncio.create_task(websocket_sender(websocket, queue))
    try:
        while True:
            # Keep connection open; receive ping or client queries
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _active_websockets.pop(websocket, None)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


from ai_analysis import router as ai_router
app.include_router(ai_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        reload=False,
        access_log=False,
    )
