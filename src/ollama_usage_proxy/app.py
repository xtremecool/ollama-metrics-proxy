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
from pathlib import Path
from typing import AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

import ollama_usage_proxy.config as _config
import ollama_usage_proxy.db as _db
import ollama_usage_proxy.usage as _usage

AppConfig = _config.AppConfig
load_config = _config.load_config
initialize_schema = _db.initialize_schema
insert_request = _db.insert_request
StreamCollector = _usage.StreamCollector

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler.

    Creates data directories, initialises the database schema on startup and logs shutdown.
    """
    config: AppConfig = app.state.config
    db_path = Path(config.database.path).expanduser()

    # Ensure parent directories exist for database and reports
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir = Path(config.reporting.output_dir).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Initialising database at %s", db_path)
    initialize_schema(str(db_path))
    logger.info(
        "Proxy listening on %s:%d -> %s",
        config.proxy.listen_host,
        config.proxy.listen_port,
        config.proxy.ollama_base_url,
    )

    yield

    logger.info("Proxy shutting down")


def create_app(config: AppConfig | None = None, config_path: str | Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Optional pre-loaded configuration. If not provided, loads from config_path.
        config_path: Path to TOML configuration file.

    Returns:
        A configured FastAPI application instance.
    """
    if config is None:
        config = load_config(config_path)

    app = FastAPI(
        title="Ollama Usage Proxy",
        description="Transparent proxy for tracking Ollama token usage metrics",
        version="0.2.0",
        lifespan=lifespan,
    )

    app.state.config = config

    # Create httpx client for forwarding requests
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0),  # Long timeout for LLM generation
    )

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def proxy(request: Request, path: str) -> Response:
        """Proxy handler - forwards all requests to Ollama and captures usage metrics.

        This is the core proxy endpoint that handles both streaming and non-streaming
        Ollama responses.
        """
        return await handle_proxy_request(request, path, config, app.state.http_client)

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "ok", "proxy": True}

    return app


async def handle_proxy_request(
    request: Request,
    path: str,
    config: AppConfig,
    client: httpx.AsyncClient,
) -> Response:
    """Handle a single proxy request.

    1. Forward the request to Ollama.
    2. Stream the response back to the caller.
    3. Extract and persist usage metrics.

    Args:
        request: The incoming FastAPI request.
        path: The URL path component.
        config: Application configuration.
        client: The httpx async client for forwarding.

    Returns:
        An HTTP response to send back to the caller.
    """
    db_path = str(Path(config.database.path).expanduser())
    ollama_base = config.proxy.ollama_base_url

    # Build target URL
    target_url = f"{ollama_base}/{path}"

    # Copy query parameters
    if str(request.url.query):
        target_url = f"{target_url}?{request.url.query}"

    # Read request body
    body = await request.body()

    # Detect if this is a streaming request by checking the request body
    is_streaming = False
    try:
        if body:
            body_json = json.loads(body)
            if isinstance(body_json, dict):
                is_streaming = body_json.get("stream", False)
    except (json.JSONDecodeError, ValueError):
        pass

    # Build headers (forward relevant ones)
    headers = {}
    for key, value in request.headers.items():
        if key.lower() in ("content-type", "authorization", "accept"):
            headers[key] = value

    method = request.method

    logger.debug(
        "Proxying %s %s -> %s (stream=%s)",
        method,
        path,
        target_url,
        is_streaming,
    )

    try:
        if is_streaming:
            # Streaming response - use StreamCollector to capture metrics.
            # The client.stream() context manager is owned by the generator so
            # the underlying HTTP connection stays open for the entire duration
            # of streaming (prevents httpx.StreamClosed errors).
            collector = StreamCollector()

            async def streaming_capture() -> AsyncIterator[bytes]:
                """Read from Ollama, feed collector, yield to client."""
                nonlocal collector

                async with client.stream(
                    method=method,
                    url=target_url,
                    content=body,
                    headers=headers,
                ) as ollama_response:
                    status_code = ollama_response.status_code
                    response_headers = dict(ollama_response.headers)
                    response_headers.pop("Content-Length", None)

                    async for chunk in ollama_response.aiter_bytes(chunk_size=None):
                        if chunk:
                            for line in chunk.split(b"\n"):
                                if line:
                                    await collector.feed_line(line)
                            yield chunk

                # Persist metrics AFTER the stream completes, but still within
                # the generator so FastAPI waits for it. Since insert_request
                # catches all exceptions internally, this is safe and will not
                # break the response if the DB write fails.
                metrics = collector.get_metrics(
                    method=method,
                    path=f"/{path}",
                    status_code=status_code,
                    streaming=True,
                )
                if metrics:
                    insert_request(db_path, metrics)
                else:
                    logger.warning("No metrics collected for streaming request to %s", path)

            return StreamingResponse(
                streaming_capture(),
                status_code=200,
            )

        else:
            # Non-streaming response - forward and read full body
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
                    extract_metrics_from_response = _models.extract_metrics_from_response

                    metrics = extract_metrics_from_response(
                        response_json,
                        method=method,
                        path=f"/{path}",
                        status_code=status_code,
                        streaming=False,
                    )
                    insert_request(db_path, metrics)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Could not parse non-streaming response from %s", path)

                return Response(
                    content=full_body,
                    status_code=status_code,
                    headers=response_headers,
                )

    except httpx.HTTPError as e:
        logger.error("HTTP error forwarding to Ollama: %s", e)

        # Record the error
        import ollama_usage_proxy.models as _models2
        UsageMetrics = _models2.UsageMetrics

        metrics = UsageMetrics(
            method=method,
            path=f"/{path}",
            error=str(e),
        )
        insert_request(db_path, metrics)

        return Response(
            content=json.dumps({"error": str(e)}),
            status_code=502,
            media_type="application/json",
        )


def main() -> None:
    """Entry point for the proxy server."""
    import argparse

    parser = argparse.ArgumentParser(description="Ollama Usage Proxy")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.toml file",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Override listen host",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override listen port",
    )
    parser.add_argument(
        "--ollama-url",
        type=str,
        default=None,
        help="Override Ollama base URL",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = getattr(logging, args.log_level.upper())
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    config = load_config(args.config)

    # Apply CLI overrides
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


if __name__ == "__main__":
    main()