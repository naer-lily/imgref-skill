"""``montage`` helper 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from imgref.errors import ImgrefError
from imgref.grid import GridSpec
from imgref.montage import run_montage
from imgref.run import SearchRequest, run_search
from tests.helpers import FakeProvider, image_handler, make_image, make_result, mock_ctx


async def _run(out_root: Path, index: int, seed: int) -> Path:
    provider = FakeProvider([[make_result(index=index)]])
    handler = image_handler({f"https://thumb.example.com/fake/{index}.png": make_image(seed)})
    async with mock_ctx(handler, label="fake") as ctx:
        outcome = await run_search(
            ctx,
            SearchRequest(
                provider=provider,
                query=f"q{index}",
                opts=None,
                limit=4,
                spec=GridSpec(cols=2, rows=2, cell=48),
                out_root=out_root,
                keep=10,
            ),
        )
    assert outcome.manifest_path is not None
    return outcome.manifest_path


async def test_montage_merges_and_renumbers(tmp_path: Path) -> None:
    first = await _run(tmp_path / "out", 1, 0x0F0F)
    second = await _run(tmp_path / "out", 2, 0x3333)
    out_path = tmp_path / "merged.jpg"
    outcome = run_montage([first, second], out_path, title="M1911")
    assert out_path.is_file()
    assert [cell.ordinal for cell in outcome.cells] == [1, 2]
    assert [cell.image.image_url for cell in outcome.cells] == [
        "https://img.example.com/fake/1.png",
        "https://img.example.com/fake/2.png",
    ]
    assert outcome.manifest_path is not None
    payload = json.loads(outcome.manifest_path.read_text(encoding="utf-8"))
    assert payload["cells"][0]["ordinal"] == 1
    assert payload["cells"][0]["id"] == "fake|https://img.example.com/fake/1.png"
    assert payload["sources"] == [str(first), str(second)]
    with Image.open(out_path) as image:
        assert image.format == "JPEG"


async def test_montage_warns_on_missing_thumbnail(tmp_path: Path) -> None:
    first = await _run(tmp_path / "out", 1, 0x0F0F)
    (first.parent / "thumbs" / "01.png").unlink()
    with pytest.raises(ImgrefError):
        run_montage([first], tmp_path / "merged.jpg")


async def test_montage_warns_when_over_capacity(tmp_path: Path) -> None:
    first = await _run(tmp_path / "out", 1, 0x0F0F)
    second = await _run(tmp_path / "out", 2, 0x3333)
    outcome = run_montage([first, second], tmp_path / "merged.jpg", spec=GridSpec(cols=1, rows=1, cell=48))
    assert len(outcome.cells) == 1
    assert any("容量" in warning for warning in outcome.warnings)


async def test_montage_can_skip_json(tmp_path: Path) -> None:
    first = await _run(tmp_path / "out", 1, 0x0F0F)
    outcome = run_montage([first], tmp_path / "merged.jpg", write_json=False)
    assert outcome.manifest_path is None
    assert not (tmp_path / "merged.json").exists()


def test_montage_rejects_unreadable_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ImgrefError):
        run_montage([bad], tmp_path / "merged.jpg")
