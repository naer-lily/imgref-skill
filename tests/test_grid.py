"""拼图渲染测试：版式数学、容量、占位格、ASCII 约束。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from imgref.grid import GridCell, GridSpec, ascii_only, ascii_title, render_grid
from tests.helpers import make_image


def test_default_spec_math() -> None:
    spec = GridSpec()
    assert spec.capacity == 12
    assert spec.width == 1536
    assert spec.height == 1310


def test_default_spec_fits_vision_budget() -> None:
    """默认版式的长边必须留在视觉模型预算内，否则会被降采样白扔算力。"""
    spec = GridSpec()
    assert max(spec.width, spec.height) <= 1536


def test_fit_max_edge_shrinks_cell() -> None:
    spec = GridSpec().fit_max_edge(1024)
    assert spec.width <= 1024
    assert spec.cell < 384


def test_fit_max_edge_keeps_spec_when_already_small() -> None:
    spec = GridSpec(cols=2, rows=2, cell=128)
    assert spec.fit_max_edge(1536) == spec


def test_fit_max_edge_never_goes_below_floor() -> None:
    spec = GridSpec().fit_max_edge(50)
    assert spec.cell >= 64


def test_for_cell_scales_caption_and_header() -> None:
    spec = GridSpec().for_cell(200)
    assert spec.cell == 200
    assert spec.caption == 22
    assert spec.header == 22
    assert GridSpec().for_cell(10).cell == 64


def test_render_grid_writes_jpeg_of_expected_size(tmp_path: Path) -> None:
    spec = GridSpec(cols=2, rows=2, cell=64)
    path = tmp_path / "grid.jpg"
    cells = [GridCell(label=str(index), data=make_image(seed)) for index, seed in enumerate((0x0F0F, 0x3333, 0x1BE4), start=1)]
    size = render_grid(path, cells, spec, "bing | test")
    assert path.is_file()
    assert size.rendered == 3
    with Image.open(path) as image:
        assert image.size == (size.width, size.height)
        assert image.format == "JPEG"


def test_render_grid_renders_placeholders(tmp_path: Path) -> None:
    spec = GridSpec(cols=2, rows=2, cell=64)
    path = tmp_path / "grid.jpg"
    size = render_grid(path, [GridCell(label="1")], spec)
    assert size.rendered == 1
    assert path.is_file()


def test_empty_slots_have_no_caption_bar(tmp_path: Path) -> None:
    """候选比格子少时，空位只留白——不能画黑编号条（半张图黑条看着像坏了）。"""
    spec = GridSpec(cols=1, rows=2, cell=64)
    path = tmp_path / "grid.jpg"
    render_grid(path, [GridCell(label="1", data=make_image(0x0F0F))], spec)
    with Image.open(path) as image:
        first_caption_y = spec.header + spec.pad + spec.cell + spec.caption // 2
        bottom_y = spec.header + spec.pad + spec.cell + spec.caption + spec.gap + spec.cell // 2
        first = image.getpixel((spec.pad + 4, first_caption_y))
        bottom = image.getpixel((spec.pad + 4, bottom_y))
    assert isinstance(first, tuple) and sum(first) < 200, "第一行应有黑编号条"
    assert isinstance(bottom, tuple) and sum(bottom) > 600, "第二行（空位）应留白"


def test_render_grid_truncates_to_capacity(tmp_path: Path) -> None:
    spec = GridSpec(cols=1, rows=1, cell=64)
    cells = [GridCell(label=str(index), data=make_image(seed)) for index, seed in enumerate((0x0F0F, 0x3333), start=1)]
    size = render_grid(tmp_path / "grid.jpg", cells, spec)
    assert size.rendered == 1


def test_render_grid_survives_undecodable_bytes(tmp_path: Path) -> None:
    spec = GridSpec(cols=1, rows=1, cell=64)
    path = tmp_path / "grid.jpg"
    size = render_grid(path, [GridCell(label="1", data=b"not an image")], spec)
    assert size.rendered == 1
    assert path.is_file()


def test_labels_change_pixels(tmp_path: Path) -> None:
    """编号真的画上去了：换编号必须换像素。"""
    spec = GridSpec(cols=1, rows=1, cell=64)
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    render_grid(first, [GridCell(label="1", data=make_image(), note="x.com")], spec)
    render_grid(second, [GridCell(label="2", data=make_image(), note="x.com")], spec)
    assert first.read_bytes() != second.read_bytes()


def test_ascii_only_drops_cjk() -> None:
    assert ascii_only("M1911各角度图片") == "M1911"
    assert ascii_only("  hello world  ") == "hello world"
    assert ascii_only("中文") == ""


def test_ascii_title_joins_and_drops_empty() -> None:
    assert ascii_title("bing", None, "M1911正面") == "bing | M1911"
    assert ascii_title("", None) == ""
    assert ascii_title("a" * 100).count("a") == 34
