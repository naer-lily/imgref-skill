"""数据类型测试：自包含 ID、尺寸标签、参数声明。"""

from __future__ import annotations

import pytest

from imgref.errors import IdError
from imgref.types import ID_SEP, Arg, ImageResult, Page, parse_ref_id


def test_ref_id_is_self_contained() -> None:
    result = ImageResult(provider="bing", image_url="https://x.com/a.jpg")
    assert result.ref_id == f"bing{ID_SEP}https://x.com/a.jpg"
    assert parse_ref_id(result.ref_id) == ("bing", "https://x.com/a.jpg")


def test_thumb_falls_back_to_image_url() -> None:
    assert ImageResult(provider="p", image_url="https://x/a.jpg").thumb == "https://x/a.jpg"
    assert ImageResult(provider="p", image_url="https://x/a.jpg", thumb_url="https://x/t.jpg").thumb == "https://x/t.jpg"


def test_size_label() -> None:
    assert ImageResult(provider="p", image_url="u", width=1200, height=900).size_label == "1200x900"
    assert ImageResult(provider="p", image_url="u").size_label == "?"
    assert ImageResult(provider="p", image_url="u", width=1200).size_label == "?"


def test_parse_ref_id_keeps_pipe_inside_url() -> None:
    provider, url = parse_ref_id("bing|https://x.com/a|b.jpg")
    assert provider == "bing"
    assert url == "https://x.com/a|b.jpg"


@pytest.mark.parametrize(
    "raw",
    [
        "no-separator",
        "|https://x.com/a.jpg",
        "bing|",
        "  ",
        "bing|ftp://x.com/a.jpg",
        "bing|javascript:alert(1)",
    ],
)
def test_parse_ref_id_rejects_bad_input(raw: str) -> None:
    with pytest.raises(IdError):
        parse_ref_id(raw)


def test_page_defaults() -> None:
    page = Page(())
    assert page.next_cursor is None
    assert list(page.results) == []


def test_arg_defaults() -> None:
    arg = Arg("--x")
    assert arg.help == ""
    assert arg.choices is None
    assert arg.default is None
    assert arg.metavar is None
    assert arg.action is None
    assert arg.type is None
