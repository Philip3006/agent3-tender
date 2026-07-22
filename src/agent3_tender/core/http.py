from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_UA = "agent3-tender/0.1 (https://github.com/agent3-tender)"
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            headers={"User-Agent": _UA},
            follow_redirects=True,
        )
    return _client


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.ConnectError | httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    client = _get_client()
    logger.debug("GET %s params=%s", url, params)
    resp = await client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def post_json(
    url: str,
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
) -> dict:
    client = _get_client()
    logger.debug("POST %s", url)
    resp = await client.post(url, json=json, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
