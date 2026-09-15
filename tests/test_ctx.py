"""HTTP 通道测试：重试、状态码分类、体积上限、缓存、JSON 解析。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from imgref.ctx import open_ctx
from imgref.errors import ANTIBOT_HINT, NETWORK_HINT, ProviderError
from tests.helpers import make_image, mock_ctx

Handler = Callable[[httpx.Request], httpx.Response]


def _counting(responses: list[httpx.Response]) -> tuple[list[int], Handler]:
    """返回 (计数列表, handler)：第 n 次请求用 responses[n-1]，越界后重复最后一个。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        index = min(len(calls), len(responses)) - 1
        return responses[index]

    return calls, handler


async def test_get_text_retries_then_succeeds() -> None:
    calls, handler = _counting([httpx.Response(500), httpx.Response(200, text="ok")])
    async with mock_ctx(handler) as ctx:
        assert await ctx.get_text("https://x.com/a") == "ok"
    assert len(calls) == 2


async def test_retry_exhausted_raises_http() -> None:
    calls, handler = _counting([httpx.Response(503, text="down")])
    async with mock_ctx(handler) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_text("https://x.com/a")
    assert len(calls) == 2
    assert info.value.kind == "http"
    assert info.value.provider == "test"


async def test_404_is_not_retried() -> None:
    calls, handler = _counting([httpx.Response(404, text="nope")])
    async with mock_ctx(handler) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_text("https://x.com/a")
    assert len(calls) == 1
    assert info.value.kind == "http"


@pytest.mark.parametrize("status", [401, 403, 429, 451])
async def test_antibot_statuses_are_classified(status: int) -> None:
    calls, handler = _counting([httpx.Response(status)])
    async with mock_ctx(handler, attempts=1) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_text("https://x.com/a")
    assert len(calls) == 1
    assert info.value.kind == "antibot"
    assert info.value.hint() == ANTIBOT_HINT


async def test_transport_error_is_network_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with mock_ctx(handler) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_text("https://x.com/a")
    assert info.value.kind == "network"
    assert info.value.hint() == NETWORK_HINT


async def test_image_size_cap() -> None:
    async with mock_ctx(lambda request: httpx.Response(200, content=b"x" * 200, headers={"content-type": "image/png"}), max_bytes=100) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_image("https://x.com/a.png")
    assert "上限" in info.value.message


async def test_image_rejects_html() -> None:
    async with mock_ctx(lambda request: httpx.Response(200, text="<html>nope</html>", headers={"content-type": "text/html"})) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_image("https://x.com/a.png")
    assert "不是图片" in info.value.message


async def test_image_rejects_tiny_payload() -> None:
    async with mock_ctx(lambda request: httpx.Response(200, content=b"tiny", headers={"content-type": "image/png"})) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_image("https://x.com/a.png")
    assert "过小" in info.value.message


async def test_image_accepts_octet_stream() -> None:
    payload = make_image()
    async with mock_ctx(lambda request: httpx.Response(200, content=payload, headers={"content-type": "application/octet-stream"})) as ctx:
        assert await ctx.get_image("https://x.com/a.png") == payload


async def test_get_json_parse_error() -> None:
    async with mock_ctx(lambda request: httpx.Response(200, text="not json", headers={"content-type": "application/json"})) as ctx:
        with pytest.raises(ProviderError) as info:
            await ctx.get_json("https://x.com/a.json")
    assert info.value.kind == "parse"


async def test_post_json_sends_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    async with mock_ctx(handler) as ctx:
        payload = await ctx.post_json("https://x.com/api", body={"q": "cat"})
    assert payload == {"ok": True}
    assert seen["method"] == "POST"
    assert b"cat" in seen["body"]


async def test_image_cache_avoids_second_request(cache_dir: Path) -> None:
    calls, handler = _counting([httpx.Response(200, content=make_image(), headers={"content-type": "image/png"})])
    async with mock_ctx(handler, cache_dir=cache_dir) as ctx:
        first = await ctx.get_image("https://x.com/a.png")
        second = await ctx.get_image("https://x.com/a.png")
    assert first == second
    assert len(calls) == 1
    async with mock_ctx(handler, cache_dir=cache_dir) as ctx:
        third = await ctx.get_image("https://x.com/a.png")
    assert third == first
    assert len(calls) == 1


async def test_no_cache_means_no_reuse(cache_dir: Path) -> None:
    calls, handler = _counting([httpx.Response(200, content=make_image(), headers={"content-type": "image/png"})])
    async with mock_ctx(handler) as ctx:
        await ctx.get_image("https://x.com/a.png")
        await ctx.get_image("https://x.com/a.png")
    assert len(calls) == 2


async def test_open_ctx_wires_options(tmp_path: Path) -> None:
    async with open_ctx(label="bing", cache_dir=tmp_path, proxy="http://127.0.0.1:9") as ctx:
        assert ctx.label == "bing"
        assert ctx.cache is not None
        assert ctx.cache.enabled
    async with open_ctx(label="bing", cache_dir=tmp_path, no_cache=True) as ctx:
        assert ctx.cache is not None
        assert not ctx.cache.enabled
    async with open_ctx(label="bing") as ctx:
        assert ctx.cache is None


async def test_default_user_agent_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 UA 必须诚实报出工具自己——浏览器 UA 会被维基媒体这类站点直接 403。"""
    monkeypatch.delenv("IMGREF_USER_AGENT", raising=False)
    async with open_ctx() as ctx:
        agent = ctx._client.headers["user-agent"]  # noqa: SLF001 - 只在这里读一次客户端默认头
    assert agent.startswith("imgref/")


async def test_user_agent_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMGREF_USER_AGENT", "custom/9")
    async with open_ctx() as ctx:
        assert ctx._client.headers["user-agent"] == "custom/9"  # noqa: SLF001
