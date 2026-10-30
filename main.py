"""
Main FastAPI application for RSyslog Web.
Integrates Web UI, REST API, WebSockets, and asynchronous Syslog listeners.
"""

import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

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

from alerter import process_alert, send_test_alert
from config import config
from database import (
    clear_all_logs,
    get_db_connection,
    get_filter_options,
    get_logs,
    get_stats,
    init_db,
    insert_log,
)
from syslog_parser import parse_syslog_message
from syslog_server import (
    broadcast_log,
    register_log_subscriber,
    server_manager,
    unregister_log_subscriber,
)
from version import get_version_info

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rsyslog.main")

# Active WebSocket connections
_active_websockets: List[WebSocket] = []


async def websocket_broadcaster(log_entry: Dict[str, Any]) -> None:
    """Broadcasts a log dictionary to all connected WebSocket clients."""
    if not _active_websockets:
        return
    payload = json.dumps(log_entry)
    for ws in list(_active_websockets):
        try:
            await ws.send_text(payload)
        except Exception:
            if ws in _active_websockets:
                _active_websockets.remove(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: initializes DB, Syslog server, and listeners."""
    logger.info("Initializing RSyslog Web database...")
    init_db()

    # Register broadcaster for real-time live logs
    register_log_subscriber(websocket_broadcaster)

    # Start UDP & TCP Syslog servers
    logger.info("Starting Syslog background listeners...")
    await server_manager.start()

    logger.info(
        f"RSyslog Web started successfully! Open http://{config.WEB_HOST}:{config.WEB_PORT} in your browser."
    )
    yield

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


class WebhookLogPayload(BaseModel):
    """Schema for direct HTTP Webhook JSON ingestion."""
    level: Optional[str] = Field("INFO", description="DEBUG, INFO, WARN, ERROR, CRITICAL")
    host: Optional[str] = Field(None, description="Sender host/ip")
    app: Optional[str] = Field(None, description="Application or service name")
    facility: Optional[str] = Field("user", description="Syslog facility")
    message: str = Field(..., description="Log message text")
    timestamp: Optional[str] = Field(None, description="ISO timestamp override")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the single-page dashboard HTML."""
    from pathlib import Path

    template_path = Path(__file__).parent / "templates" / "index.html"
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard template not found")
    return HTMLResponse(content=template_path.read_text(encoding="utf-8"))


@app.get("/api/logs")
async def api_get_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    level: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    app: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """Retrieves paginated and filtered logs."""
    logs, total = get_logs(
        page=page,
        per_page=per_page,
        level=level,
        host=host,
        app=app,
        search=search,
    )
    return {
        "logs": logs,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


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
        parsed = parse_syslog_message(raw_body, client_ip=client_ip)
        saved = insert_log(
            host=parsed["host"],
            app=parsed["app"],
            level=parsed["level"],
            facility=parsed["facility"],
            message=parsed["message"],
            raw=parsed["raw"],
            timestamp=parsed["timestamp"],
        )

    # Trigger alerts and realtime broadcast
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
    format: str = Query("csv", regex="^(csv|json)$"),
    level: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    app: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
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
        writer.writerow(["ID", "Timestamp", "Level", "Host", "App", "Facility", "Message"])
        for row in logs:
            writer.writerow([
                row.get("id"),
                row.get("timestamp"),
                row.get("level"),
                row.get("host"),
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


@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time log streaming WebSocket."""
    await websocket.accept()
    _active_websockets.append(websocket)
    try:
        while True:
            # Keep connection open; receive ping or client queries
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _active_websockets:
            _active_websockets.remove(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        reload=False,
    )
