"""``preview`` / ``download`` 取图测试。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from imgref.errors import IdError
from imgref.grab import GrabRequest, collect_ids, grab
from imgref.providers.base import Provider
from tests.helpers import FakeProvider, image_handler, make_image, mock_ctx

PROVIDERS: dict[str, Provider] = {"fake": FakeProvider([])}
TARGET = "https://img.example.com/fake/1.png"
REF = f"fake|{TARGET}"


async def test_download_writes_original_bytes(tmp_path: Path) -> None:
    payload = make_image(0x0F0F, 128)
    async with mock_ctx(image_handler({TARGET: payload}), label="-") as ctx:
        files, warnings = await grab(ctx, GrabRequest(ids=(REF,), out_dir=tmp_path), PROVIDERS)
    assert not warnings
    assert len(files) == 1
    assert files[0].path.read_bytes() == payload
    assert files[0].source_size == "128x128"
    assert not files[0].downscaled
    assert files[0].path.parent == tmp_path


async def test_preview_downscales_and_reports(tmp_path: Path) -> None:
    payload = make_image(0x0F0F, 256)
    async with mock_ctx(image_handler({TARGET: payload}), label="-") as ctx:
        files, _ = await grab(ctx, GrabRequest(ids=(REF,), out_dir=tmp_path, max_edge=64), PROVIDERS)
    assert files[0].downscaled
    assert files[0].source_size == "256x256"
    assert files[0].path.suffix == ".jpg"
    with Image.open(files[0].path) as image:
        assert max(image.size) == 64


async def test_preview_keeps_small_images_as_is(tmp_path: Path) -> None:
    payload = make_image(0x0F0F, 64)
    async with mock_ctx(image_handler({TARGET: payload}), label="-") as ctx:
        files, _ = await grab(ctx, GrabRequest(ids=(REF,), out_dir=tmp_path, max_edge=1024), PROVIDERS)
    assert not files[0].downscaled
    assert files[0].path.suffix == ".png"


async def test_grab_multiple_ids_keeps_order_and_names(tmp_path: Path) -> None:
    first = make_image(0x0F0F)
    second = make_image(0x3333)
    mapping = {TARGET: first, "https://img.example.com/fake/2.png": second}
    ids = (REF, "fake|https://img.example.com/fake/2.png")
    async with mock_ctx(image_handler(mapping), label="-") as ctx:
        files, warnings = await grab(ctx, GrabRequest(ids=ids, out_dir=tmp_path), PROVIDERS)
    assert not warnings
    assert [item.ref_id for item in files] == list(ids)
    assert files[0].path.name.startswith("01-")
    assert files[1].path.name.startswith("02-")


async def test_grab_isolates_single_failure(tmp_path: Path) -> None:
    payload = make_image(0x0F0F)
    ids = (REF, "fake|https://img.example.com/fake/missing.png")
    async with mock_ctx(image_handler({TARGET: payload}), label="-") as ctx:
        files, warnings = await grab(ctx, GrabRequest(ids=ids, out_dir=tmp_path), PROVIDERS)
    assert len(files) == 1
    assert len(warnings) == 1
    assert "取图失败" in warnings[0]


async def test_grab_rejects_unknown_provider(tmp_path: Path) -> None:
    async with mock_ctx(image_handler({}), label="-") as ctx:
        with pytest.raises(IdError) as info:
            await grab(ctx, GrabRequest(ids=("nope|https://x/a.jpg",), out_dir=tmp_path), PROVIDERS)
    assert "nope" in str(info.value)


async def test_grab_rejects_malformed_id(tmp_path: Path) -> None:
    async with mock_ctx(image_handler({}), label="-") as ctx:
        with pytest.raises(IdError):
            await grab(ctx, GrabRequest(ids=("no-separator",), out_dir=tmp_path), PROVIDERS)


async def test_grab_creates_output_dir(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested"
    async with mock_ctx(image_handler({TARGET: make_image()}), label="-") as ctx:
        files, _ = await grab(ctx, GrabRequest(ids=(REF,), out_dir=target), PROVIDERS)
    assert files[0].path.parent == target


def test_collect_ids_dedupes_and_keeps_order() -> None:
    assert collect_ids(("a", "b", "a"), pick=("c",)) == ["c", "a", "b"]


def test_collect_ids_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "ids.txt"
    path.write_text("one\n\n two \none\n", encoding="utf-8")
    assert collect_ids((), ids_file=path) == ["one", "two"]


def test_collect_ids_reports_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        collect_ids((), ids_file=tmp_path / "missing.txt")


def test_grab_request_defaults() -> None:
    request = GrabRequest(ids=("a",), out_dir=Path("."))
    assert request.max_edge == 0


def test_downscale_handles_undecodable_bytes() -> None:
    from imgref.grab import _downscale

    data, changed = _downscale(b"not an image", 64)
    assert data == b"not an image"
    assert changed is False


def test_size_label_handles_undecodable_bytes() -> None:
    from imgref.grab import _size_label

    assert _size_label(b"nope") == "?"
    buffer = io.BytesIO()
    Image.new("RGB", (7, 9)).save(buffer, format="PNG")
    assert _size_label(buffer.getvalue()) == "7x9"
