"""FastAPI proxy server that sits between Cline and Ollama.

Forwards all requests to Ollama, streams responses back to Cline,
and captures usage metrics from the final response payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import ollama_usage_proxy.config as _config
from ollama_usage_proxy import __version__
import ollama_usage_proxy.db as _db
import ollama_usage_proxy.usage as _usage
from ollama_usage_proxy.metrics_buffer import TimeSeriesRingBuffer

AppConfig = _config.AppConfig
load_config = _config.load_config
initialize_schema = _db.initialize_schema
insert_request = _db.insert_request
ainsert_system_metrics = _db.ainsert_system_metrics
get_recent_system_metrics = _db.get_recent_system_metrics
SystemMetricsPoint = _db.SystemMetricsPoint
StreamCollector = _usage.StreamCollector

logger = logging.getLogger(__name__)

# ── WebSocket connection registry ────────────────────────────────────────

_connected_clients: list[WebSocket] = []
_connected_clients_lock = asyncio.Lock()

# ── Global metrics buffer (60-second sliding window) ─────────────────────

metrics_buffer = TimeSeriesRingBuffer()

# ── Shared GPU state (written by telemetry loop, read by broadcast loop) ─

_latest_gpu: tuple[bool, float | None, float | None, float | None] | None = None

# ── Request lifecycle tracking (in-flight status) ────────────────────────

class RequestLifecycle:
    """Thread-safe tracker for proxy request lifecycle states."""
    __slots__ = ("_in_flight_count", "_is_generating", "_lock")

    def __init__(self) -> None:
        self._in_flight_count: int = 0
        self._is_generating: bool = False
        self._lock = asyncio.Lock()

    async def request_started(self) -> None:
        async with self._lock:
            self._in_flight_count += 1

    async def mark_generating(self) -> None:
        async with self._lock:
            self._is_generating = True

    async def request_completed(self) -> None:
        async with self._lock:
            self._in_flight_count -= 1
            if self._in_flight_count <= 0:
                self._in_flight_count = 0
                self._is_generating = False

    @property
    def status(self) -> str:
        if self._in_flight_count == 0:
            return "idle"
        elif self._is_generating:
            return "generating"
        else:
            return "thinking"

    @property
    def in_flight_count(self) -> int:
        return self._in_flight_count


request_lifecycle = RequestLifecycle()

# ── Session-level cumulative token counters ──────────────────────────────

class SessionCounters:
    """Cumulative token totals for the current session."""
    __slots__ = ("total_input_tokens", "total_output_tokens", "_lock")

    def __init__(self) -> None:
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self._lock = asyncio.Lock()

    async def add(self, input_tokens: int, output_tokens: int) -> None:
        async with self._lock:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens


session_counters = SessionCounters()


# ── Background telemetry poller ──────────────────────────────────────────

async def start_telemetry_loop(db_path: str, monitor, interval: float = 1.0) -> None:
    """Continuously sample GPU telemetry and persist to SQLite."""
    global _latest_gpu

    if not monitor.active:
        logger.info("Telemetry poller skipped (GPU monitor not active)")
        return

    try:
        while True:
            snapshot = monitor.get_telemetry()
            if snapshot is not None:
                metric_point = SystemMetricsPoint(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    gpu_temp_c=snapshot.gpu_temp_c,
                    gpu_power_w=snapshot.gpu_power_w,
                    gpu_util_pct=snapshot.gpu_util_pct,
                )
                await ainsert_system_metrics(db_path, metric_point)
                metrics_buffer.push_gpu_data(
                    snapshot.gpu_temp_c,
                    snapshot.gpu_power_w,
                    snapshot.gpu_util_pct,
                )
                _latest_gpu = (True, snapshot.gpu_temp_c, snapshot.gpu_power_w, snapshot.gpu_util_pct)
            else:
                _latest_gpu = (False, None, None, None)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.debug("Telemetry poller cancelled")
    finally:
        monitor.close()


async def _send_to_all_clients(payload: dict[str, Any]) -> None:
    """Send JSON payload to all connected WebSocket clients; remove stale connections."""
    async with _connected_clients_lock:
        clients = list(_connected_clients)
    stale: list[WebSocket] = []
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            stale.append(ws)
    if stale:
        async with _connected_clients_lock:
            for ws in stale:
                if ws in _connected_clients:
                    _connected_clients.remove(ws)


async def _broadcast_live_metrics() -> None:
    """Unified broadcast: 60-slot ordered snapshot + live GPU + lifecycle state."""
    ordered_snapshot = metrics_buffer.get_ordered_snapshot()

    if _latest_gpu is not None:
        gpu_online, gpu_temp_c, gpu_power_w, gpu_util_pct = _latest_gpu
    else:
        gpu_online, gpu_temp_c, gpu_power_w, gpu_util_pct = False, None, None, None

    payload = {
        "type": "metrics",
        "status": request_lifecycle.status,
        "in_flight_requests": request_lifecycle.in_flight_count,
        "total_session_input_tokens": session_counters.total_input_tokens,
        "total_session_output_tokens": session_counters.total_output_tokens,
        "gpu_online": gpu_online,
        "gpu_temp_c": gpu_temp_c,
        "gpu_power_w": gpu_power_w,
        "gpu_util_pct": gpu_util_pct,
        "snapshot": ordered_snapshot,
    }
    await _send_to_all_clients(payload)


async def _metrics_broadcast_loop(interval: float = 1.0) -> None:
    """Broadcast the full 60-slot ring-buffer snapshot every second."""
    try:
        while True:
            await _broadcast_live_metrics()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.debug("Metrics broadcast loop cancelled")


async def _broadcast_request_complete(model: str | None, input_tokens: int, output_tokens: int,
                                      output_tps: float | None) -> None:
    """Broadcast a request-completion event to the dashboard."""
    payload = {
        "type": "request",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model or "unknown",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "output_tps": output_tps or 0.0,
    }
    await _send_to_all_clients(payload)


def _record_request_complete(
    end_time: datetime,
    output_tokens: int,
    input_tokens: int = 0,
) -> None:
    """Seed the ring buffer with a completed request (synchronous, fire-and-forget)."""
    try:
        metrics_buffer.push_ollama_request(
            completion_time_ms=end_time.timestamp() * 1000.0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception:
        logger.debug("Failed to record request completion in metrics buffer", exc_info=True)


def _silent_task_callback(task: asyncio.Task) -> None:
    """Suppress logged exceptions from fire-and-forget ring buffer writes."""
    try:
        if task.done() and not task.cancelled():
            task.exception()
    except Exception:
        pass


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    config: AppConfig = app.state.config
    db_path = Path(config.database.path).expanduser()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir = Path(config.reporting.output_dir).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Initialising database at %s", db_path)
    await asyncio.to_thread(initialize_schema, str(db_path))
    logger.info(
        "Ollama Metrics Proxy v%s - listening on %s:%d -> %s",
        __version__,
        config.proxy.listen_host,
        config.proxy.listen_port,
        config.proxy.ollama_base_url,
    )

    from ollama_usage_proxy.system_telemetry import get_monitor
    monitor = get_monitor()
    app.state.gpu_monitor = monitor

    if monitor and monitor.active:
        logger.info("Starting GPU telemetry poller (interval=1.0s)")
        app.state.telemetry_task = asyncio.create_task(
            start_telemetry_loop(str(db_path), monitor, interval=1.0)
        )
    else:
        logger.info("GPU telemetry disabled (non-Linux or no NVIDIA hardware detected)")
        app.state.telemetry_task = None

    logger.info("Starting metrics broadcast loop (interval=1.0s)")
    app.state.metrics_tick_task = asyncio.create_task(
        _metrics_broadcast_loop(interval=1.0)
    )

    yield

    logger.info("Proxy shutting down")
    await app.state.http_client.aclose()

    if getattr(app.state, "telemetry_task", None):
        app.state.telemetry_task.cancel()
        try:
            await app.state.telemetry_task
        except asyncio.CancelledError:
            pass

    if getattr(app.state, "metrics_tick_task", None):
        app.state.metrics_tick_task.cancel()
        try:
            await app.state.metrics_tick_task
        except asyncio.CancelledError:
            pass


# ── App factory ──────────────────────────────────────────────────────────

def create_app(config: AppConfig | None = None, config_path: str | Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = load_config(config_path)

    app = FastAPI(
        title="Ollama Usage Proxy",
        description="Transparent proxy for tracking Ollama token usage metrics",
        version=__version__,
        lifespan=lifespan,
    )

    app.state.config = config
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15.0, read=None, write=30.0, pool=10.0),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint with proxy status, GPU, lifecycle, and session counters."""
        monitor = getattr(app.state, "gpu_monitor", None)
        return {
            "status": "ok",
            "version": __version__,
            "proxy": True,
            "gpu_online": bool(monitor and monitor.active),
            "in_flight_requests": request_lifecycle.in_flight_count,
            "total_session_input_tokens": session_counters.total_input_tokens,
            "total_session_output_tokens": session_counters.total_output_tokens,
        }

    @app.websocket("/api/ws/telemetry")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        async with _connected_clients_lock:
            _connected_clients.append(websocket)
        try:
            while True:
                _ = await websocket.receive_text()
        except (WebSocketDisconnect, Exception):
            async with _connected_clients_lock:
                if websocket in _connected_clients:
                    _connected_clients.remove(websocket)

    @app.get("/api/history")
    async def get_history(limit: int = 100) -> dict:
        db_path = str(Path(config.database.path).expanduser())
        points = await asyncio.to_thread(get_recent_system_metrics, db_path, limit=limit)
        monitor = getattr(app.state, "gpu_monitor", None)
        return {
            "gpu_online": bool(monitor and monitor.active),
            "metrics": [
                {
                    "timestamp": p.timestamp,
                    "gpu_temp_c": p.gpu_temp_c,
                    "gpu_power_w": p.gpu_power_w,
                    "gpu_util_pct": p.gpu_util_pct,
                }
                for p in points
            ],
        }

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/dashboard")
    async def dashboard_page() -> Response:
        index_path = static_dir / "index.html"
        if index_path.is_file():
            return Response(content=index_path.read_text(), media_type="text/html")
        return Response(content="<h1>Dashboard not found</h1>", status_code=404, media_type="text/html")

    @app.get("/uplot-test")
    async def uplot_test_page() -> Response:
        test_path = static_dir / "uplot-test.html"
        if test_path.is_file():
            return Response(content=test_path.read_text(), media_type="text/html")
        return Response(content="<h1>Test page not found</h1>", status_code=404, media_type="text/html")

    @app.get("/")
    async def root_page() -> Response:
        index_path = static_dir / "index.html"
        if index_path.is_file():
            return Response(content=index_path.read_text(), media_type="text/html")
        return Response(
            content='{"message": "Ollama Usage Proxy", "dashboard": "/dashboard"}',
            media_type="application/json",
        )

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxy(request: Request, path: str) -> Response:
        return await handle_proxy_request(request, path, config, app.state.http_client)

    return app


# ── Proxy request handler ────────────────────────────────────────────────

async def handle_proxy_request(
    request: Request,
    path: str,
    config: AppConfig,
    client: httpx.AsyncClient,
) -> Response:
    """Handle a single proxy request forwarding to Ollama."""
    db_path = str(Path(config.database.path).expanduser())
    ollama_base = config.proxy.ollama_base_url
    target_url = f"{ollama_base}/{path}"

    if str(request.url.query):
        target_url = f"{target_url}?{request.url.query}"

    body = await request.body()
    is_streaming = False
    try:
        if body:
            body_json = json.loads(body)
            if isinstance(body_json, dict):
                is_streaming = body_json.get("stream", False)
    except (json.JSONDecodeError, ValueError):
        pass

    headers = {}
    for key, value in request.headers.items():
        if key.lower() in ("content-type", "authorization", "accept"):
            headers[key] = value

    method = request.method
    logger.debug("Proxying %s %s -> %s (stream=%s)", method, path, target_url, is_streaming)

    await request_lifecycle.request_started()

    try:
        if is_streaming:
            collector = StreamCollector()
            _generating_flag = False

            async def streaming_capture() -> AsyncIterator[bytes]:
                nonlocal collector, _generating_flag
                status_code = 200
                line_buffer = bytearray()

                try:
                    async with client.stream(
                        method=method,
                        url=target_url,
                        content=body,
                        headers=headers,
                    ) as ollama_response:
                        status_code = ollama_response.status_code

                        async for chunk in ollama_response.aiter_bytes(chunk_size=None):
                            if chunk:
                                line_buffer.extend(chunk)
                                while b"\n" in line_buffer:
                                    line, line_buffer = line_buffer.split(b"\n", 1)
                                    if line:
                                        await collector.feed_line(bytes(line))
                                        if not _generating_flag:
                                            try:
                                                jd = json.loads(line)
                                                if isinstance(jd, dict) and jd.get("done") is False:
                                                    _generating_flag = True
                                                    await request_lifecycle.mark_generating()
                                            except (json.JSONDecodeError, ValueError):
                                                pass
                                yield bytes(chunk)

                    if line_buffer:
                        await collector.feed_line(bytes(line_buffer))

                except asyncio.CancelledError:
                    logger.warning("Streaming request to %s cancelled by client", path)
                    metrics = collector.get_metrics(
                        method=method, path=f"/{path}", status_code=status_code, streaming=True
                    )
                    if metrics:
                        metrics.error = "Stream disconnected by client"
                        await asyncio.to_thread(insert_request, db_path, metrics)
                        await session_counters.add(metrics.input_tokens, metrics.output_tokens)
                    raise
                except Exception as exc:
                    logger.error("Streaming error for %s: %s", path, exc)
                    metrics = collector.get_metrics(
                        method=method, path=f"/{path}", status_code=status_code, streaming=True
                    )
                    if metrics:
                        metrics.error = f"Stream error: {exc}"
                        await asyncio.to_thread(insert_request, db_path, metrics)
                        await session_counters.add(metrics.input_tokens, metrics.output_tokens)
                    raise
                else:
                    metrics = collector.get_metrics(
                        method=method, path=f"/{path}", status_code=status_code, streaming=True
                    )
                    if metrics:
                        await asyncio.to_thread(insert_request, db_path, metrics)
                        await session_counters.add(metrics.input_tokens, metrics.output_tokens)
                        await _broadcast_request_complete(
                            metrics.model,
                            metrics.input_tokens,
                            metrics.output_tokens,
                            metrics.output_tokens_per_second,
                        )
                        request_end = datetime.now(timezone.utc)
                        _task = asyncio.create_task(
                            asyncio.to_thread(
                                _record_request_complete,
                                request_end,
                                metrics.output_tokens,
                                metrics.input_tokens,
                            )
                        )
                        _task.add_done_callback(_silent_task_callback)
                    else:
                        logger.warning("No metrics collected for streaming request to %s", path)
                finally:
                    await request_lifecycle.request_completed()

            return StreamingResponse(streaming_capture(), status_code=200)

        else:
            try:
                async with client.stream(
                    method=method,
                    url=target_url,
                    content=body,
                    headers=headers,
                ) as ollama_response:
                    status_code = ollama_response.status_code
                    response_headers = dict(ollama_response.headers)
                    full_body = await ollama_response.aread()

                    try:
                        response_json = json.loads(full_body)
                        import ollama_usage_proxy.models as _models
                        metrics = _models.extract_metrics_from_response(
                            response_json,
                            method=method,
                            path=f"/{path}",
                            status_code=status_code,
                            streaming=False,
                        )
                        await asyncio.to_thread(insert_request, db_path, metrics)
                        await session_counters.add(metrics.input_tokens, metrics.output_tokens)
                        await _broadcast_request_complete(
                            metrics.model,
                            metrics.input_tokens,
                            metrics.output_tokens,
                            metrics.output_tokens_per_second,
                        )
                        request_end = datetime.now(timezone.utc)
                        _task = asyncio.create_task(
                            asyncio.to_thread(
                                _record_request_complete,
                                request_end,
                                metrics.output_tokens,
                                metrics.input_tokens,
                            )
                        )
                        _task.add_done_callback(_silent_task_callback)
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("Could not parse non-streaming response from %s", path)

                    return Response(
                        content=full_body,
                        status_code=status_code,
                        headers=response_headers,
                    )
            finally:
                await request_lifecycle.request_completed()

    except httpx.HTTPError as e:
        logger.error("HTTP error forwarding to Ollama: %s", e)
        await request_lifecycle.request_completed()

        import ollama_usage_proxy.models as _models2
        metrics = _models2.UsageMetrics(
            method=method,
            path=f"/{path}",
            error=str(e),
        )
        await asyncio.to_thread(insert_request, db_path, metrics)

        return Response(
            content=json.dumps({"error": "Bad gateway"}),
            status_code=502,
            media_type="application/json",
        )
    except Exception as e:
        logger.error("Unexpected error in proxy handler for %s: %s", path, e)
        await request_lifecycle.request_completed()
        raise


def main() -> None:
    """Entry point for the proxy server."""
    import argparse

    parser = argparse.ArgumentParser(description="Ollama Usage Proxy")
    parser.add_argument("--config", type=str, default=None, help="Path to config.toml file")
    parser.add_argument("--host", type=str, default=None, help="Override listen host")
    parser.add_argument("--port", type=int, default=None, help="Override listen port")
    parser.add_argument("--ollama-url", type=str, default=None, help="Override Ollama base URL")
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level",
    )

    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper())
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    config = load_config(args.config)

    if args.host:
        config.proxy.listen_host = args.host
    if args.port:
        config.proxy.listen_port = args.port
    if args.ollama_url:
        config.proxy.ollama_base_url = args.ollama_url.rstrip("/")

    app = create_app(config)

    uvicorn.run(
        app,
        host=config.proxy.listen_host,
        port=config.proxy.listen_port,
        log_level=args.log_level,
    )


def get_app() -> FastAPI:
    """Create a fully configured FastAPI app using environment/config defaults."""
    config = load_config(None)
    return create_app(config)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()