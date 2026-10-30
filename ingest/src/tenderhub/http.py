"""Rate-limited, retrying HTTP client shared by all connectors."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from typing import Any

import h2.exceptions
import httpx

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; tenderhub/0.1; public procurement aggregator)"
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status, self.url, self.body = status, url, body


class RateLimiter:
    """Spaces requests at least 1/rps seconds apart."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


def _retry_after(r: httpx.Response) -> float | None:
    v = r.headers.get("retry-after")
    if not v:
        return None
    if v.isdigit():
        return float(v)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(v).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Http:
    """Wraps a shared httpx.AsyncClient with a per-connector rate limit and retries.

    Retries transport errors and 408/425/429/5xx with exponential backoff (honouring Retry-After).
    Raises HttpError for other 4xx/5xx after retries are exhausted.
    """

    def __init__(self, client: httpx.AsyncClient, rps: float = 2.0, max_retries: int = 6,
                 log: logging.Logger | None = None):
        self.client = client
        self.limiter = RateLimiter(rps)
        self.max_retries = max_retries
        self.log = log or logging.getLogger("tenderhub.http")
        self.requests = 0

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            await self.limiter.wait()
            self.requests += 1
            try:
                r = await self.client.request(method, url, **kw)
            except (httpx.TransportError, h2.exceptions.ProtocolError) as e:
                if attempt == self.max_retries:
                    raise
                delay = min(60.0, 2 ** attempt) + random.random()
                self.log.warning("%s %s failed (%s), retry in %.1fs", method, url, e, delay)
                await asyncio.sleep(delay)
                continue
            if r.status_code in RETRY_STATUSES and attempt < self.max_retries:
                delay = _retry_after(r) or min(60.0, 2 ** attempt) + random.random()
                self.log.warning("%s %s -> %s, retry in %.1fs", method, url, r.status_code, delay)
                await asyncio.sleep(delay)
                continue
            if r.status_code >= 400:
                raise HttpError(r.status_code, str(r.url), r.text)
            return r
        raise AssertionError("unreachable")

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, **kw)

    async def get_json(self, url: str, **kw: Any) -> Any:
        return (await self.get(url, **kw)).json()

    async def post_json(self, url: str, **kw: Any) -> Any:
        return (await self.post(url, **kw)).json()

    async def get_text(self, url: str, **kw: Any) -> str:
        return (await self.get(url, **kw)).text


def make_client(user_agent: str = DEFAULT_USER_AGENT, timeout: float = 60.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        # HTTP/1.1 on purpose: with many sources syncing at once, servers closing HTTP/2 connections
        # surfaced as h2 ProtocolErrors that httpx does not map to retryable transport errors
        http2=False,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=20.0),
        headers={"User-Agent": user_agent, "Accept-Language": "en,pl;q=0.8,*;q=0.5"},
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    )
