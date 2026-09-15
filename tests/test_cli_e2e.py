"""CLI 端到端测试：把网络层换成 MockTransport，跑完整的 search / download / montage。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from imgref import cli
from imgref.ctx import Ctx
from imgref.errors import ProviderError
from imgref.providers.base import Provider
from imgref.types import Arg, Cursor, Page
from tests.helpers import FakeProvider, image_handler, make_image, make_result, mock_ctx

Handler = Callable[[httpx.Request], httpx.Response]


class BoomProvider:
    """一上来就失败，用来验证退出码与提示。"""

    name: str = "boom"
    label: str = "爆炸图源"
    args: Sequence[Arg] = ()
    requires: Sequence[str] = ()

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Any) -> Page:
        """直接抛错。"""
        raise ProviderError("boom", "network", "connect timeout")


def _patch(monkeypatch: pytest.MonkeyPatch, handler: Handler, provider: Provider) -> None:
    """把 CLI 的网络层与图源注册表换成测试替身。"""

    @asynccontextmanager
    async def fake_open_ctx(**kwargs: Any) -> AsyncIterator[Ctx]:
        async with mock_ctx(handler, label=str(kwargs.get("label", "-"))) as ctx:
            yield ctx

    monkeypatch.setattr(cli, "open_ctx", fake_open_ctx)
    monkeypatch.setattr(cli, "get_provider", lambda name: provider)
    monkeypatch.setattr(cli, "PROVIDERS", {provider.name: provider})


def _thumbs(*seeds: int) -> dict[str, bytes]:
    return {f"https://thumb.example.com/fake/{index}.png": make_image(seed) for index, seed in enumerate(seeds, start=1)}


def _originals(*seeds: int) -> dict[str, bytes]:
    return {f"https://img.example.com/fake/{index}.png": make_image(seed) for index, seed in enumerate(seeds, start=1)}


def test_search_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = FakeProvider([[make_result(index=1), make_result(index=2)]])
    _patch(monkeypatch, image_handler(_thumbs(0x0F0F, 0x3333)), provider)
    code = cli.main(["search", "fake", "m1911", "--out", str(tmp_path), "--limit", "2", "--cols", "2", "--rows", "1", "--cell", "48"])
    out = capsys.readouterr().out
    assert code == 0
    assert "grid: " in out and "data: " in out
    assert "fake|https://img.example.com/fake/1.png" in out
    assert "1200x900" in out
    assert "不要自己拼 URL" in out
    grid = next(tmp_path.rglob("grid.jpg"))
    assert grid.is_file()
    assert next(tmp_path.rglob("results.json")).is_file()


def test_search_reports_warnings_on_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = FakeProvider([[make_result(index=1), make_result(index=2)]])
    _patch(monkeypatch, image_handler({"https://thumb.example.com/fake/1.png": make_image()}), provider)
    cli.main(["search", "fake", "q", "--out", str(tmp_path), "--cell", "48"])
    out = capsys.readouterr().out
    assert "warn: 缩略图下载失败" in out


def test_search_exit_code_2_when_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch(monkeypatch, image_handler({}), FakeProvider([[]]))
    assert cli.main(["search", "fake", "q", "--out", str(tmp_path)]) == 2
    assert "没有返回结果" in capsys.readouterr().err


def test_search_exit_code_1_on_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch(monkeypatch, image_handler({}), BoomProvider())
    assert cli.main(["search", "boom", "q", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "connect timeout" in err
    assert "HTTPS_PROXY" in err


def test_quiet_still_shows_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = FakeProvider([[make_result(index=1), make_result(index=2)]])
    _patch(monkeypatch, image_handler({"https://thumb.example.com/fake/1.png": make_image()}), provider)
    cli.main(["search", "fake", "q", "--out", str(tmp_path), "--cell", "48", "-q"])
    out = capsys.readouterr().out
    assert "[fake] 'q'" not in out
    assert "warn:" in out


def test_download_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = make_image(0x0F0F, 96)
    _patch(monkeypatch, image_handler({"https://img.example.com/fake/1.png": payload}), FakeProvider([]))
    ref = "fake|https://img.example.com/fake/1.png"
    out_dir = tmp_path / "refs"
    assert cli.main(["download", ref, "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    written = list(out_dir.iterdir())
    assert len(written) == 1
    assert written[0].read_bytes() == payload
    assert str(written[0].resolve()) in out


def test_preview_with_pick_and_from(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider([[make_result(index=1), make_result(index=2)]])
    _patch(monkeypatch, image_handler(_thumbs(0x0F0F, 0x3333) | _originals(0x0F0F, 0x3333)), provider)
    assert cli.main(["search", "fake", "q", "--out", str(tmp_path), "--cell", "48"]) == 0
    manifest = next(tmp_path.rglob("results.json"))
    target = tmp_path / "picked"
    assert cli.main(["preview", "--pick", "2", "--from", str(manifest), "--out", str(target), "--max", "32"]) == 0
    files = list(target.iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("01-")


def test_download_ids_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, image_handler({"https://img.example.com/fake/1.png": make_image()}), FakeProvider([]))
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("fake|https://img.example.com/fake/1.png\n", encoding="utf-8")
    assert cli.main(["download", "--ids-file", str(ids_file), "--out", str(tmp_path / "out")]) == 0


def test_montage_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider([[make_result(index=1)]])
    _patch(monkeypatch, image_handler(_thumbs(0x0F0F)), provider)
    assert cli.main(["search", "fake", "a", "--out", str(tmp_path), "--cell", "48"]) == 0
    first = next(tmp_path.rglob("results.json"))
    assert cli.main(["search", "fake", "b", "--out", str(tmp_path), "--cell", "48"]) == 0
    manifests = sorted(tmp_path.rglob("results.json"))
    assert len(manifests) == 2
    out_path = tmp_path / "merged.jpg"
    assert cli.main(["montage", str(manifests[0]), str(manifests[1]), "--out", str(out_path)]) == 0
    assert out_path.is_file()
    assert (tmp_path / "merged.json").is_file()


def test_montage_missing_manifest_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["montage", str(tmp_path / "nope.json"), "--out", str(tmp_path / "m.jpg")]) == 1
    assert "无法读取" in capsys.readouterr().err


def test_default_out_root_is_absolute() -> None:
    assert cli.default_out_root().is_absolute()


def test_default_cache_dir_is_absolute() -> None:
    assert cli.default_cache_dir().is_absolute()
