"""Thin Canvas REST API client: retries, pagination, streaming downloads."""

from __future__ import annotations

import hashlib
import random
import threading
import time
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx

from . import __version__

RETRY_STATUS = {429, 500, 502, 503, 504}


class CanvasError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(CanvasError):
    """The token is missing, invalid or expired."""


class ForbiddenError(CanvasError):
    """The token is fine but this resource is hidden or missing (e.g. Files tab disabled)."""


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff: float = 1.0,
        max_concurrency: int = 8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff = backoff
        # Callers may use many threads; this caps how many API calls are in flight at
        # once, which keeps us well inside Canvas's rate limit. Downloads are not capped
        # here (the download pool size limits them).
        self._slots = threading.BoundedSemaphore(max(1, max_concurrency))
        # httpx drops the Authorization header when a redirect leaves this host,
        # so file downloads redirected to S3 / inst-fs do not leak the token.
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1/",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": f"canvas-pull/{__version__}",
            },
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            transport=transport,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    def __enter__(self) -> CanvasClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- low level -----------------------------------------------------------

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        try:
            delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            delay = 0.0
        delay = max(delay, self.backoff * (2**attempt)) + random.uniform(0, self.backoff / 2)
        time.sleep(min(delay, 60.0))

    @staticmethod
    def _is_throttled(resp: httpx.Response) -> bool:
        # Canvas signals throttling with 403 + "Rate Limit Exceeded".
        return resp.status_code == 403 and "rate limit exceeded" in resp.text.lower()

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return f"HTTP {resp.status_code}"
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                return str(errors[0].get("message", errors[0]))
            if errors:
                return str(errors)
            if body.get("message"):
                return str(body["message"])
        return f"HTTP {resp.status_code}"

    def _send(self, url: str, *, params: Any = None, stream: bool = False) -> httpx.Response:
        attempt = 0
        while True:
            try:
                request = self._http.build_request("GET", url, params=params)
                with nullcontext() if stream else self._slots:
                    resp = self._http.send(request, stream=stream)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise CanvasError(f"Network error: {exc}") from exc
                self._sleep(attempt, None)
                attempt += 1
                continue

            if resp.is_success:
                return resp

            resp.read()
            resp.close()
            retryable = resp.status_code in RETRY_STATUS or self._is_throttled(resp)
            if retryable and attempt < self.max_retries:
                self._sleep(attempt, resp.headers.get("Retry-After"))
                attempt += 1
                continue

            message = self._error_message(resp)
            if resp.status_code == 401:
                # Canvas also uses 401 for "user not authorized to perform that action";
                # only a bad token comes with WWW-Authenticate / "Invalid access token".
                if "www-authenticate" in resp.headers or "access token" in message.lower():
                    raise AuthError(
                        "Access token rejected. It may have expired: create a new one "
                        "in Canvas (Account → Settings) and run `canvas-pull init`.",
                        401,
                    )
                raise ForbiddenError(message, 401)
            if resp.status_code in (403, 404):
                raise ForbiddenError(message, resp.status_code)
            raise CanvasError(f"{message} ({resp.request.url})", resp.status_code)

    # -- public --------------------------------------------------------------

    def get(self, path: str, params: Any = None) -> Any:
        return self._send(path, params=params).json()

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Yield every item of a list endpoint, following Link: rel="next"."""
        url: str | None = path
        query: dict[str, Any] | None = {"per_page": 100, **(params or {})}
        while url:
            resp = self._send(url, params=query)
            data = resp.json()
            if not isinstance(data, list):
                raise CanvasError(f"Expected a list from {path}, got {type(data).__name__}")
            yield from data
            url = resp.links.get("next", {}).get("url")
            query = None  # the next URL already carries the query string

    def download(self, url: str, dest: Path) -> tuple[int, str]:
        """Stream `url` into `dest`. Returns (size, sha256)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        attempt = 0
        while True:
            digest = hashlib.sha256()
            size = 0
            resp = self._send(url, stream=True)
            try:
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 16):
                        fh.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                return size, digest.hexdigest()
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise CanvasError(f"Download interrupted: {exc}") from exc
                self._sleep(attempt, None)
                attempt += 1
            finally:
                resp.close()
