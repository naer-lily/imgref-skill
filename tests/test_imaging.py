"""图像工具测试：aHash、汉明距离、尺寸探测、扩展名嗅探。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from imgref.errors import ImgrefError
from imgref.imaging import HASH_BITS, ahash, hamming, image_size, sniff_ext
from tests.helpers import BLOCK_SEEDS, make_image


def test_ahash_is_deterministic_and_64_bits() -> None:
    data = make_image(0x0F0F)
    digest = ahash(data)
    assert len(digest) == HASH_BITS
    assert set(digest) <= {"0", "1"}
    assert digest == ahash(data)


def test_ahash_distinguishes_structural_differences() -> None:
    digests = {seed: ahash(make_image(seed)) for seed in BLOCK_SEEDS}
    assert len(set(digests.values())) == len(BLOCK_SEEDS)


def test_ahash_is_invariant_to_brightness_shift() -> None:
    """整体亮度偏移对 aHash 是不变量——这正说明夹具必须用结构性差异。"""
    data = make_image(0x3333)
    with Image.open(io.BytesIO(data)) as image:
        shifted = image.point(lambda value: min(int(value * 0.6) + 40, 255))
        buffer = io.BytesIO()
        shifted.save(buffer, format="PNG")
    assert ahash(buffer.getvalue()) == ahash(data)


def test_ahash_solid_colors_are_degenerate() -> None:
    """纯色是退化输入：不同颜色得到同一个指纹（记得别这么写夹具）。"""
    hashes = set()
    for color in ((255, 255, 255), (10, 10, 10), (200, 30, 30)):
        image = Image.new("RGB", (64, 64), color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        hashes.add(ahash(buffer.getvalue()))
    assert hashes == {"0" * HASH_BITS}


def test_ahash_rejects_garbage() -> None:
    with pytest.raises(ImgrefError):
        ahash(b"definitely not an image")


def test_hamming() -> None:
    assert hamming("1010", "1001") == 2
    assert hamming("1111", "1111") == 0
    with pytest.raises(ImgrefError):
        hamming("101", "1010")


def test_image_size() -> None:
    assert image_size(make_image(0x0F0F, 48)) == (48, 48)
    assert image_size(b"nope") is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (make_image(0x0F0F), ".png"),
    ],
)
def test_sniff_ext_from_content(payload: bytes, expected: str) -> None:
    assert sniff_ext(payload) == expected


def test_sniff_ext_falls_back_to_url_then_unknown() -> None:
    assert sniff_ext(b"garbage", "https://x.com/a/b.JPEG?x=1") == ".jpg"
    assert sniff_ext(b"garbage", "https://x.com/a/b.png") == ".png"
    assert sniff_ext(b"garbage", "https://x.com/a/b") == ".img"
