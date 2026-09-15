"""pytest 共用夹具。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.helpers import make_image


@pytest.fixture
def image_bytes() -> Callable[[int, int], bytes]:
    """返回"按 seed 造图"的工厂。"""

    def factory(seed: int = 0x0F0F, size: int = 64) -> bytes:
        return make_image(seed, size)

    return factory


@pytest.fixture
def out_root(tmp_path: Path) -> Path:
    """一次运行的输出根目录。"""
    return tmp_path / ".out"


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """缓存目录。"""
    return tmp_path / "cache"
