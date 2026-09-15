"""imgref —— 无状态的多图源参考图搜索工具。

对外只有 CLI（``scripts/imgref.py``）。设计要点见 ``AGENTS.md``，
用法见 ``SKILL.md``。

三个动词加一个 helper：

* ``search``   —— 一个图源 + 一个查询串 → 一张编号拼图 + 一张候选表
* ``preview``  —— 按自包含 ID 取图到缓存目录（可降采样），给调用方看大图
* ``download`` —— 按自包含 ID 取全分辨率原图到指定目录
* ``montage``  —— 把多轮 search 的拼图合并成一张（边缘 helper，不影响主流程）
"""

from __future__ import annotations

from typing import Final

__all__ = ["__version__"]

__version__: Final = "1.0.0"
