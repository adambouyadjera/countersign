"""Opt-in HTTP transcript transport; authentication values are always masked."""

from __future__ import annotations

import json
from pathlib import Path

import httpx


class NessieTranscriptTransport(httpx.AsyncBaseTransport):
    def __init__(self, key: str, report: Path, *, transport: httpx.AsyncBaseTransport | None = None):
        self._secret = key
        self._report = report
        self._transport = transport if transport is not None else httpx.AsyncHTTPTransport()

    def _clean(self, value: str) -> str:
        return value.replace(self._secret, "[REDACTED]") if self._secret else value

    def _emit(self, value: dict) -> None:
        encoded = self._clean(json.dumps(value, ensure_ascii=True))
        self._report.parent.mkdir(parents=True, exist_ok=True)
        with self._report.open("a", encoding="utf-8") as output:
            output.write(encoded + "\n")
        print(encoded, flush=True)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url.copy_set_param("key", "[REDACTED]")
        headers = {
            name: "[REDACTED]" if name.lower() in {"authorization", "cookie", "x-api-key"} else value
            for name, value in request.headers.items()
        }
        body = request.content.decode("utf-8") if request.content else None
        try:
            body = json.loads(body) if body else None
        except ValueError:
            pass
        self._emit({"request": {"method": request.method, "url": str(url), "headers": headers, "json": body}})
        try:
            response = await self._transport.handle_async_request(request)
            raw = (await response.aread()).decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            self._emit({"response": {"status": response.status_code, "body": body}})
            return response
        except httpx.HTTPError:
            self._emit({"response": {"error": "HTTP transport failed"}})
            raise

    async def aclose(self):
        await self._transport.aclose()
