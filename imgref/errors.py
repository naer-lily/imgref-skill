"""imgref 的异常层次与退出码约定。

退出码（由 :func:`imgref.cli.main` 翻译）：

====  ============================================
0     成功（哪怕部分图源失败、部分缩略图下载失败）
1     用法错误 / ID 非法 / 抓取失败
2     一个结果都没有（搜索为空，或全部被排除）
====  ============================================
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ANTIBOT_HINT",
    "EXIT_EMPTY",
    "EXIT_OK",
    "EXIT_USAGE",
    "NETWORK_HINT",
    "FetchError",
    "IdError",
    "ImgrefError",
    "NoResultsError",
    "ProviderError",
    "UsageError",
]

EXIT_OK: Final = 0
EXIT_USAGE: Final = 1
EXIT_EMPTY: Final = 2

NETWORK_HINT: Final = "网络不可达：检查网络连接，或设置 HTTPS_PROXY / NO_PROXY 后重试"
"""网络类失败时给调用方的可操作提示。"""

ANTIBOT_HINT: Final = "目标站拒绝（可能触发反爬）：稍后重试，或换一个 --provider"
"""4xx 类失败时给调用方的可操作提示。"""


class ImgrefError(Exception):
    """所有 imgref 异常的基类。"""

    exit_code: int = EXIT_USAGE


class UsageError(ImgrefError):
    """命令行用法错误。"""


class NoResultsError(ImgrefError):
    """一个结果都没有。"""

    exit_code = EXIT_EMPTY


class IdError(UsageError):
    """ID 格式非法，或 ID 指向的图源不存在。"""


class FetchError(ImgrefError):
    """按 ID 取图失败。"""


class ProviderError(ImgrefError):
    """单个图源的失败。

    这一层异常永远被隔离：一个图源挂了不能拖垮同一次调用里的其他工作，
    所以它带够上下文（图源名 + 失败种类 + 原始消息）后被上层收集成 warning。
    """

    def __init__(self, provider: str, kind: str, message: str) -> None:
        """记录失败归属与种类。

        Args:
            provider: 图源名（``bing`` / ``ddg`` / …），或 ``-`` 表示与图源无关。
            kind: ``network`` | ``antibot`` | ``http`` | ``parse`` | ``auth``。
            message: 原始错误消息。
        """
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.kind = kind
        self.message = message

    def hint(self) -> str:
        """返回该失败种类对应的可操作提示（没有就返回空串）。"""
        if self.kind == "network":
            return NETWORK_HINT
        if self.kind in {"antibot", "http"}:
            return ANTIBOT_HINT
        return ""
