"""图源注册表。

扩展一个新图源只需要两步：

1. 在 ``imgref/providers/`` 下新建一个模块，写一个**普通类**：
   声明 ``name`` / ``label`` / ``args`` / ``requires``，实现 ``async def search(...)``。
   不需要继承任何东西——``Provider`` 是 Protocol，静态检查一样生效。
2. 把它的实例加进下面的 ``_ALL``。

``_ALL`` 那里就是静态一致性检查点：mypy 会逐个核对每个实例是否满足
:class:`~imgref.providers.base.Provider` 协议。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from imgref.errors import UsageError
from imgref.providers.base import Provider, missing_env
from imgref.providers.bing import Bing
from imgref.providers.ddg import DuckDuckGo
from imgref.providers.duitang import Duitang
from imgref.providers.openverse import Openverse
from imgref.providers.safebooru import Safebooru
from imgref.providers.serper import Serper
from imgref.providers.wikimedia import Wikimedia
from imgref.providers.yandere import Yandere

__all__ = ["PROVIDERS", "get_provider", "provider_rows"]

# 通用网络图源 → 图库 API → 中文图片站 → 付费接口。
_ALL: Final[tuple[Provider, ...]] = (
    Bing(),
    DuckDuckGo(),
    Wikimedia(),
    Openverse(),
    Yandere(),
    Safebooru(),
    Duitang(),
    Serper(),
)

PROVIDERS: Final[dict[str, Provider]] = {provider.name: provider for provider in _ALL}


def get_provider(name: str) -> Provider:
    """按名字取图源。

    Args:
        name: 图源名。

    Returns:
        对应的图源适配器。

    Raises:
        UsageError: 名字未知；消息里会列出全部可用图源。
    """
    try:
        return PROVIDERS[name]
    except KeyError:
        known = "、".join(sorted(PROVIDERS))
        raise UsageError(f"未知图源 {name!r}；可用：{known}") from None


def provider_rows() -> list[tuple[str, str, str]]:
    """返回 ``(名字, 说明, 私有参数摘要)`` 列表，供帮助里的图源索引使用。

    需要环境变量但当前缺失的图源，名字后面会带 ``★``。
    """
    rows: list[tuple[str, str, str]] = []
    for name in sorted(PROVIDERS):
        provider = PROVIDERS[name]
        flags = _flag_summary(provider.args)
        label = provider.label
        absent = missing_env(provider)
        if absent:
            label = f"★{label}（未设置 {'/'.join(absent)}）"
        rows.append((name, label, flags))
    return rows


def _flag_summary(args: Sequence[object]) -> str:
    """把私有参数的 flag 拼成一行摘要。"""
    flags: list[str] = []
    for arg in args:
        raw = getattr(arg, "flags", None)
        if isinstance(raw, str):
            flags.append(raw)
        elif isinstance(raw, Sequence):
            flags.extend(str(item) for item in raw)
    return " ".join(flags) if flags else "-"
