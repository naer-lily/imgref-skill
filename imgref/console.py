"""面向 AI 调用方的输出约定。

一条硬规则：**结果与警告都走 stdout**，只有 ``-v`` 的内部调试走 stderr。
调用方（AI）只需要读一个流就能拿到"两个绝对路径 + 候选表 + 失败警告"，
不会因为漏读 stderr 而把"某个图源挂了"误解成"关键词太窄"。
"""

from __future__ import annotations

import sys
import unicodedata
from typing import TextIO

__all__ = ["Console", "display_width"]


def display_width(text: str) -> int:
    """文本在终端里的显示宽度（CJK 算两列），用于对齐帮助文本。"""
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


class Console:
    """极简输出器，负责 quiet / verbose 策略。"""

    def __init__(self, *, quiet: bool = False, verbose: bool = False, stream: TextIO | None = None, err: TextIO | None = None) -> None:
        """记录输出策略。

        Args:
            quiet: 静默常规信息（警告与错误仍然输出）。
            verbose: 把内部调试信息打到 stderr。
            stream: stdout 替身（测试用）。
            err: stderr 替身（测试用）。
        """
        self.quiet = quiet
        self.verbose = verbose
        self._out = stream if stream is not None else sys.stdout
        self._err = err if err is not None else sys.stderr

    def out(self, message: str = "") -> None:
        """输出常规信息到 stdout。"""
        print(message, file=self._out)

    def warn(self, message: str) -> None:
        """输出警告到 stdout（quiet 下也不隐藏——调用方必须看见失败）。"""
        print(f"warn: {message}", file=self._out)

    def debug(self, message: str) -> None:
        """输出调试信息到 stderr（仅 ``-v``）。"""
        if self.verbose:
            print(f"debug: {message}", file=self._err)

    def error(self, message: str) -> None:
        """输出错误到 stderr。"""
        print(f"imgref: {message}", file=self._err)
