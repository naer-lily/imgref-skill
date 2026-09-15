"""HTTP 通道：provider 与网络世界之间唯一的接口。

超时、重试、退避、并发上限、响应体上限、代理、缓存、User-Agent 全部在这里统一，
**provider 不直接使用 httpx**——它只描述"要发什么请求"，于是这些策略对所有图源一致。

代理来自环境变量：``httpx`` 的 ``trust_env=True`` 原生认 ``HTTPS_PROXY`` /
``HTTP_PROXY`` / ``NO_PROXY``，不需要我们自己实现。
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final, TypeVar

import httpx

from imgref.cache import BlobCache
from imgref.errors import ProviderError

__all__ = ["BROWSER_UA", "DEFAULT_UA", "Ctx", "open_ctx"]

_T = TypeVar("_T")

HONEST_UA: Final = "imgref/1.0 (reference image search; +https://github.com/naer-lily/imgref-skill)"
"""默认 User-Agent：**诚实**地把工具自己和联系方式报上去。

这不是洁癖：维基媒体等站点会对浏览器 UA 直接返回 403（"Please respect our robot
policy"），而带说明的 UA 畅通无阻。需要伪装成浏览器的抓取型图源（bing / ddg）
在自己的请求里覆盖它——那是图源自己的事。
"""

BROWSER_UA: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
"""抓取型图源用的浏览器 UA。"""

DEFAULT_UA: Final = HONEST_UA
"""框架默认 UA；可用环境变量 ``IMGREF_USER_AGENT`` 覆盖。"""

_RETRY_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})
_ANTIBOT_STATUS: Final[frozenset[int]] = frozenset({401, 403, 429, 451})
_DEFAULT_MAX_BYTES: Final = 12 * 1024 * 1024
_MIN_IMAGE_BYTES: Final = 64


class _Retryable(Exception):
    """内部信号：这次失败值得重试，重试耗尽后转换成 :class:`ProviderError`。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def classify_status(url: str, status: int) -> ProviderError:
    """把 HTTP 状态码翻译成带可操作提示的 :class:`ProviderError`。"""
    kind = "antibot" if status in _ANTIBOT_STATUS else "http"
    return ProviderError("-", kind, f"HTTP {status} for {url}")


class Ctx:
    """单次调用内共享的网络上下文。

    由 :func:`open_ctx` 创建；生命周期与一次 CLI 调用一致。
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        cache: BlobCache | None,
        label: str = "-",
        max_bytes: int = _DEFAULT_MAX_BYTES,
        attempts: int = 2,
        concurrency: int = 8,
        backoff: float = 0.3,
    ) -> None:
        """记录网络策略。

        Args:
            client: 已配置好的 httpx 客户端。
            cache: 图片字节缓存；``None`` 表示不缓存。
            label: 出错信息里显示的图源名。
            max_bytes: 单个响应体上限。
            attempts: 总尝试次数（含首次）。
            concurrency: 图片下载并发上限。
            backoff: 重试退避基数（秒）。
        """
        self._client = client
        self._cache = cache
        self.label = label
        self._max_bytes = max_bytes
        self._attempts = max(attempts, 1)
        self._sem = asyncio.Semaphore(max(concurrency, 1))
        self._backoff = backoff

    @property
    def cache(self) -> BlobCache | None:
        """当前使用的缓存（可能为 ``None``）。"""
        return self._cache

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """发一个请求，带重试与状态码分类。

        Args:
            method: HTTP 方法。
            url: 目标 URL。
            **kw: 透传给 httpx 的参数（``params`` / ``headers`` / ``json`` / …）。

        Returns:
            状态码小于 400 的响应。

        Raises:
            ProviderError: 重试耗尽，或遇到不该重试的状态码。
        """

        async def once() -> httpx.Response:
            try:
                response = await self._client.request(method, url, **kw)
            except httpx.TransportError as exc:
                raise _Retryable("network", f"{type(exc).__name__}: {exc}") from exc
            if response.status_code in _RETRY_STATUS:
                raise _Retryable("antibot" if response.status_code in _ANTIBOT_STATUS else "http", f"HTTP {response.status_code} for {url}")
            if response.status_code >= 400:
                raise self._error(classify_status(url, response.status_code))
            return response

        return await self._with_retry(f"GET {url}", once)

    async def get_text(self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None) -> str:
        """取文本响应（HTML 抓取用）。"""
        response = await self.request("GET", url, params=params, headers=dict(headers or {}))
        return response.text

    async def get_json(self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None) -> Any:
        """取 JSON 响应；解析失败时抛 :class:`ProviderError`（kind=parse）。"""
        response = await self.request("GET", url, params=params, headers=dict(headers or {}))
        return self._decode_json(response, url)

    async def post_json(self, url: str, *, body: Any, headers: Mapping[str, str] | None = None) -> Any:
        """发一个 JSON POST 并解析响应（给需要 POST 的图源用）。"""
        response = await self.request("POST", url, json=body, headers=dict(headers or {}))
        return self._decode_json(response, url)

    def _decode_json(self, response: httpx.Response, url: str) -> Any:
        """解析响应 JSON；失败时抛 :class:`ProviderError`（kind=parse）。"""
        try:
            return response.json()
        except ValueError as exc:
            raise self._error(ProviderError(self.label, "parse", f"响应不是合法 JSON：{url}")) from exc

    async def get_image(self, url: str, *, headers: Mapping[str, str] | None = None) -> bytes:
        """取图片字节：先查缓存，未命中再流式下载（带并发上限与体积上限）。

        Args:
            url: 图片 URL。
            headers: 附加请求头（图源自己决定的 referer / cookie / token）。

        Returns:
            图片字节。

        Raises:
            ProviderError: 下载失败、体积超限，或响应明显不是图片。
        """
        if self._cache is not None:
            hit = self._cache.get(url)
            if hit is not None:
                return hit
        merged = dict(headers or {})

        async with self._sem:
            data = await self._with_retry(f"GET {url}", lambda: self._download(url, merged))
        if self._cache is not None:
            self._cache.put(url, data)
        return data

    async def _download(self, url: str, headers: Mapping[str, str]) -> bytes:
        """流式下载一次，并施加体积上限与内容类型检查。"""
        try:
            async with self._client.stream("GET", url, headers=dict(headers)) as response:
                if response.status_code in _RETRY_STATUS:
                    raise _Retryable("antibot" if response.status_code in _ANTIBOT_STATUS else "http", f"HTTP {response.status_code} for {url}")
                if response.status_code >= 400:
                    raise self._error(classify_status(url, response.status_code))
                content_type = response.headers.get("content-type", "")
                if content_type and not content_type.startswith(("image/", "application/octet-stream")):
                    raise self._error(ProviderError(self.label, "http", f"不是图片（{content_type}）：{url}"))
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._max_bytes:
                        raise self._error(ProviderError(self.label, "http", f"响应体超过 {self._max_bytes} 字节上限：{url}"))
                    chunks.append(chunk)
        except httpx.TransportError as exc:
            raise _Retryable("network", f"{type(exc).__name__}: {exc}") from exc
        data = b"".join(chunks)
        if len(data) < _MIN_IMAGE_BYTES:
            raise self._error(ProviderError(self.label, "http", f"图片内容过小（{len(data)} 字节）：{url}"))
        return data

    async def _with_retry(self, what: str, fn: Callable[[], Awaitable[_T]]) -> _T:
        """执行 ``fn``，对可重试失败退避重试，耗尽后抛 :class:`ProviderError`。"""
        last: _Retryable = _Retryable("network", "未发起任何尝试")
        for attempt in range(self._attempts):
            if attempt:
                await asyncio.sleep(self._backoff * (2 ** (attempt - 1)) + random.random() * 0.1)
            try:
                return await fn()
            except _Retryable as exc:
                last = exc
        raise self._error(ProviderError(self.label, last.kind, f"{what} 失败：{last.message}"))

    def _error(self, error: ProviderError) -> ProviderError:
        """把不带图源名的错误补上当前 label 后返回。"""
        if error.provider == "-" and self.label != "-":
            return ProviderError(self.label, error.kind, error.message)
        return error


@asynccontextmanager
async def open_ctx(
    *,
    label: str = "-",
    timeout: float = 15.0,
    proxy: str | None = None,
    cache_dir: Path | None = None,
    cache_mb: int = 200,
    no_cache: bool = False,
    concurrency: int = 8,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    attempts: int = 2,
    user_agent: str | None = None,
) -> AsyncIterator[Ctx]:
    """打开一个 :class:`Ctx`，退出时关闭底层连接池。

    Args:
        label: 出错信息里显示的图源名。
        timeout: 总超时（秒）；连接超时取 ``min(timeout, 8)``。
        proxy: 显式代理 URL；``None`` 时交给 httpx 读环境变量。
        cache_dir: 图片缓存目录；``None`` 表示不缓存。
        cache_mb: 缓存容量上限（MB）。
        no_cache: 禁用缓存读写。
        concurrency: 图片下载并发上限。
        max_bytes: 单个响应体上限。
        attempts: 总尝试次数（含首次）。
        user_agent: 默认 User-Agent；``None`` 时读 ``IMGREF_USER_AGENT``，再退回 :data:`DEFAULT_UA`。

    Yields:
        可用的 :class:`Ctx`。
    """
    if user_agent is None:
        user_agent = os.environ.get("IMGREF_USER_AGENT") or DEFAULT_UA
    limits = httpx.Limits(max_connections=max(concurrency * 2, 8), max_keepalive_connections=max(concurrency, 4))
    options: dict[str, Any] = {
        "follow_redirects": True,
        "trust_env": True,
        "limits": limits,
        "timeout": httpx.Timeout(timeout, connect=min(timeout, 8.0)),
        "headers": {"User-Agent": user_agent, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    }
    if proxy:
        options["proxy"] = proxy
    async with httpx.AsyncClient(**options) as client:
        cache = BlobCache(cache_dir, cap_mb=cache_mb, enabled=not no_cache) if cache_dir is not None else None
        yield Ctx(
            client=client,
            cache=cache,
            label=label,
            max_bytes=max_bytes,
            attempts=attempts,
            concurrency=concurrency,
        )
