"""
Asynchronous Syslog Server for RSyslog Web.
Provides UDP (RFC 3164/5424) and TCP listeners running concurrently with FastAPI.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from config import config
from database import insert_log
from syslog_parser import parse_syslog_message
import alerter

logger = logging.getLogger("rsyslog.server")

# Global list of event broadcaster callbacks (e.g., WebSockets)
_broadcast_subscribers: List[Callable[[Dict[str, Any]], Any]] = []


def register_log_subscriber(callback: Callable[[Dict[str, Any]], Any]) -> None:
    """Registers a listener callback for incoming parsed logs."""
    if callback not in _broadcast_subscribers:
        _broadcast_subscribers.append(callback)


def unregister_log_subscriber(callback: Callable[[Dict[str, Any]], Any]) -> None:
    """Removes a listener callback."""
    if callback in _broadcast_subscribers:
        _broadcast_subscribers.remove(callback)


async def broadcast_log(log_entry: Dict[str, Any]) -> None:
    """Dispatches a new log entry to all active subscribers (e.g. WebSockets)."""
    for sub in list(_broadcast_subscribers):
        try:
            res = sub(log_entry)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            logger.debug(f"Subscriber error: {e}")


def handle_incoming_log_sync(raw_data: str, client_ip: str) -> Dict[str, Any]:
    """Synchronous parsing and database storage of a log entry."""
    parsed = parse_syslog_message(raw_data, client_ip=client_ip)
    saved = insert_log(
        host=parsed["host"],
        app=parsed["app"],
        level=parsed["level"],
        facility=parsed["facility"],
        message=parsed["message"],
        raw=parsed["raw"],
        timestamp=parsed["timestamp"],
    )
    return saved


async def handle_incoming_log(raw_data: str, client_ip: str) -> Dict[str, Any]:
    """Processes, stores, alerts, and broadcasts an incoming log."""
    # 1. Parse and store in DB
    loop = asyncio.get_running_loop()
    saved = await loop.run_in_executor(None, handle_incoming_log_sync, raw_data, client_ip)

    from monitoring import observe_log
    await observe_log(saved)
    await alerter.process_alert(saved)
    await broadcast_log(saved)

    return saved


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    """UDP listener protocol for RFC 3164 / 5424 syslog packets."""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
        logger.info(
            f"UDP Syslog listener started on {config.SYSLOG_BIND_HOST}:{config.SYSLOG_UDP_PORT}"
        )

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        try:
            # Decode with fallback for non-UTF8 devices
            try:
                raw_text = data.decode("utf-8")
            except UnicodeDecodeError:
                raw_text = data.decode("latin-1", errors="replace")

            client_ip = addr[0] if addr else "unknown"
            server_manager.enqueue(raw_text, client_ip)
        except Exception as e:
            logger.error(f"Error handling UDP datagram: {e}")

    def error_received(self, exc: Exception) -> None:
        logger.error(f"UDP Syslog error received: {exc}")


async def handle_tcp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handles an incoming TCP connection streaming syslog messages."""
    if server_manager.active_tcp_clients >= 64:
        writer.close()
        await writer.wait_closed()
        return
    server_manager.active_tcp_clients += 1
    client_addr = writer.get_extra_info("peername")
    client_ip = client_addr[0] if client_addr else "unknown"
    logger.debug(f"TCP Syslog connection established from {client_ip}")

    try:
        buffer = ""
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), 120)
            if not chunk:
                break
            try:
                buffer += chunk.decode("utf-8")
            except UnicodeDecodeError:
                buffer += chunk.decode("latin-1", errors="replace")

            if len(buffer) > 65536:
                raise ValueError("TCP message exceeds 64 KiB")

            # Split messages by newline
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    await handle_incoming_log(line, client_ip)

        # Process trailing data if any
        if buffer.strip():
            await handle_incoming_log(buffer.strip(), client_ip)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in TCP syslog client {client_ip}: {e}")
    finally:
        server_manager.active_tcp_clients -= 1
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


class SyslogServerManager:
    """Manages the lifecycle of both UDP and TCP Syslog services."""

    def __init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=2000)
        self.workers = []
        self.dropped = 0
        self.active_tcp_clients = 0
        self.udp_transport: Optional[asyncio.DatagramTransport] = None
        self.tcp_server: Optional[asyncio.Server] = None

    def enqueue(self, raw, ip):
        try:
            self.queue.put_nowait((raw, ip))
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                logger.warning("UDP queue full; dropped %s messages", self.dropped)

    async def consume(self):
        while True:
            raw, ip = await self.queue.get()
            try:
                await handle_incoming_log(raw, ip)
            except Exception:
                logger.exception("Failed processing syslog message")
            finally:
                self.queue.task_done()

    async def start(self) -> None:
        self.workers = [asyncio.create_task(self.consume()) for _ in range(4)]
        loop = asyncio.get_running_loop()

        # 1. Start UDP Listener
        if config.SYSLOG_UDP_PORT > 0:
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: SyslogUDPProtocol(),
                    local_addr=(config.SYSLOG_BIND_HOST, config.SYSLOG_UDP_PORT),
                )
                self.udp_transport = transport
            except PermissionError:
                logger.error(
                    f"PERMISSION DENIED: Cannot bind UDP port {config.SYSLOG_UDP_PORT}. "
                    f"Ports below 1024 require root/sudo. Use SYSLOG_UDP_PORT=5514 in .env"
                )
            except Exception as e:
                logger.error(f"Failed to start UDP Syslog listener: {e}")

        # 2. Start TCP Listener
        if config.SYSLOG_TCP_PORT > 0:
            try:
                self.tcp_server = await asyncio.start_server(
                    handle_tcp_client,
                    host=config.SYSLOG_BIND_HOST,
                    port=config.SYSLOG_TCP_PORT,
                )
                logger.info(
                    f"TCP Syslog listener started on {config.SYSLOG_BIND_HOST}:{config.SYSLOG_TCP_PORT}"
                )
            except PermissionError:
                logger.error(
                    f"PERMISSION DENIED: Cannot bind TCP port {config.SYSLOG_TCP_PORT}. "
                    f"Ports below 1024 require root/sudo. Use SYSLOG_TCP_PORT=5514 in .env"
                )
            except Exception as e:
                logger.error(f"Failed to start TCP Syslog listener: {e}")

    async def stop(self) -> None:
        if self.udp_transport:
            self.udp_transport.close()
            logger.info("UDP Syslog listener stopped.")

        if self.tcp_server:
            self.tcp_server.close()
            await self.tcp_server.wait_closed()
            logger.info("TCP Syslog listener stopped.")

        try:
            await asyncio.wait_for(self.queue.join(), 10)
        except asyncio.TimeoutError:
            logger.warning("Shutdown queue drain timed out")
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)


server_manager = SyslogServerManager()
