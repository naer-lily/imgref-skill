"""CLI 解析与帮助测试（不发网络请求）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from imgref.cli import GrabCmd, MontageCmd, SearchCmd, _parse, _parse_pick, main
from imgref.errors import UsageError
from imgref.grid import GridSpec
from imgref.manifest import Cell, write_manifest
from imgref.types import ImageResult


def test_root_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in ("search", "preview", "download", "montage"):
        assert command in out
    assert "自包含" in out


def test_no_arguments_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: imgref" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("imgref ")


def test_unknown_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["frobnicate"]) == 1
    assert "未知命令" in capsys.readouterr().err


def test_search_without_provider_lists_sources(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search"]) == 1
    err = capsys.readouterr().err
    assert "可用图源" in err
    assert "bing" in err


def test_search_help_is_provider_index(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "--help"]) == 0
    out = capsys.readouterr().out
    assert "可用图源" in out
    for name in ("bing", "ddg", "wikimedia", "openverse", "serper"):
        assert name in out
    assert "私参" in out


def test_search_help_for_one_provider_separates_global_and_private(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "bing", "--help"]) == 0
    out = capsys.readouterr().out
    assert "全局参数（所有命令共用）" in out
    assert "搜索参数（所有图源共用）" in out
    assert "bing 专属参数（来自 Bing）" in out
    assert "--safe" in out
    assert "--region" not in out


def test_search_help_for_ddg_shows_its_own_args(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "ddg", "--help"]) == 0
    out = capsys.readouterr().out
    assert "ddg 专属参数（来自 DuckDuckGo）" in out
    assert "--region" in out


def test_unknown_provider(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "google", "x"]) == 1
    assert "未知图源" in capsys.readouterr().err


def test_unknown_private_option_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "bing", "x", "--mime", "jpeg"]) == 1
    assert "--mime" in capsys.readouterr().err


def test_missing_query(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "bing"]) == 1
    assert "usage" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["preview", "download", "montage"])
def test_subcommand_help(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([command, "--help"]) == 0
    out = capsys.readouterr().out
    assert "全局参数（所有命令共用）" in out


def test_download_without_ids(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["download"]) == 1
    assert "没有给任何 ID" in capsys.readouterr().err


def test_pick_requires_from(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["download", "--pick", "1"]) == 1
    assert "--from" in capsys.readouterr().err


def test_montage_requires_out(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["montage", "a.json"]) == 1
    assert "--out" in capsys.readouterr().err


def test_parse_search_defaults() -> None:
    command = _parse(["search", "bing", "m1911"])
    assert isinstance(command, SearchCmd)
    assert command.provider.name == "bing"
    assert command.query == "m1911"
    assert command.limit == 12
    assert command.spec.capacity == 12
    assert command.write_manifest is True
    assert command.exclude == ()


def test_parse_search_options() -> None:
    command = _parse(["search", "bing", "m1911", "--limit", "4", "--cols", "2", "--rows", "2", "--cell", "128", "--label", "front", "--no-json", "--keep", "3"])
    assert isinstance(command, SearchCmd)
    assert command.limit == 4
    assert command.spec.capacity == 4
    assert command.label == "front"
    assert command.write_manifest is False
    assert command.keep == 3


def test_parse_search_clamps_cell_to_max_edge() -> None:
    command = _parse(["search", "bing", "q", "--cols", "4", "--cell", "384", "--max-edge", "1024"])
    assert isinstance(command, SearchCmd)
    assert command.spec.width <= 1024


def test_parse_search_exclude_is_repeatable() -> None:
    command = _parse(["search", "bing", "q", "--exclude", "a.json", "--exclude", "b.json"])
    assert isinstance(command, SearchCmd)
    assert [path.name for path in command.exclude] == ["a.json", "b.json"]


@pytest.mark.parametrize(
    ("argv", "attribute", "expected"),
    [
        (["--safe", "strict"], "safe", "strict"),
        (["--region", "cn-zh"], "region", "cn-zh"),
        (["--mime", "jpeg"], "mime", "jpeg"),
        (["--thumb-width", "512"], "thumb_width", 512),
        (["--license-type", "commercial"], "license_type", "commercial"),
        (["--gl", "us"], "gl", "us"),
    ],
)
def test_private_options_reach_provider(argv: list[str], attribute: str, expected: object) -> None:
    provider = {"--safe": "bing", "--region": "ddg", "--mime": "wikimedia", "--thumb-width": "wikimedia", "--license-type": "openverse", "--gl": "serper"}[argv[0]]
    command = _parse(["search", provider, "q", *argv])
    assert isinstance(command, SearchCmd)
    assert getattr(command.opts, attribute) == expected


def test_parse_preview_defaults() -> None:
    command = _parse(["preview", "bing|https://x/a.jpg"])
    assert isinstance(command, GrabCmd)
    assert command.kind == "preview"
    assert command.max_edge == 1024
    assert command.ids == ("bing|https://x/a.jpg",)


def test_parse_download_defaults() -> None:
    command = _parse(["download", "bing|https://x/a.jpg"])
    assert isinstance(command, GrabCmd)
    assert command.kind == "download"
    assert command.max_edge == 0


def test_parse_download_ids_file(tmp_path: Path) -> None:
    path = tmp_path / "ids.txt"
    path.write_text("a|https://x/1.jpg\n", encoding="utf-8")
    command = _parse(["download", "--ids-file", str(path)])
    assert isinstance(command, GrabCmd)
    assert command.ids == ("a|https://x/1.jpg",)


def test_parse_download_ordinals_follow_manifest(tmp_path: Path) -> None:
    """--from 里的编号要跟着 ID 一起传下去，供文件命名使用。"""
    manifest = tmp_path / "results.json"
    cells = [
        Cell(ordinal=number, image=ImageResult(provider="fake", image_url=f"https://x/{number}.png"))
        for number in (3, 7)
    ]
    write_manifest(
        manifest,
        provider="fake",
        query="q",
        label=None,
        spec=GridSpec(),
        grid_path=tmp_path / "grid.jpg",
        cells=cells,
        warnings=[],
    )
    command = _parse(["download", "--pick", "3,7", "--from", str(manifest)])
    assert isinstance(command, GrabCmd)
    assert command.ids == ("fake|https://x/3.png", "fake|https://x/7.png")
    assert command.ordinals == {"fake|https://x/3.png": 3, "fake|https://x/7.png": 7}


def test_parse_download_without_from_has_no_ordinals() -> None:
    command = _parse(["download", "fake|https://x/1.png"])
    assert isinstance(command, GrabCmd)
    assert command.ordinals == {}


def test_parse_montage() -> None:
    command = _parse(["montage", "a.json", "b.json", "--out", "merged.jpg", "--title", "x", "--cols", "2"])
    assert isinstance(command, MontageCmd)
    assert [path.name for path in command.manifests] == ["a.json", "b.json"]
    assert command.spec.cols == 2
    assert command.write_json is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, ()), ("", ()), ("1,3,7", (1, 3, 7)), ("1， 2", (1, 2)), (" 5 ", (5,))],
)
def test_parse_pick(raw: str | None, expected: tuple[int, ...]) -> None:
    assert _parse_pick(raw) == expected


def test_parse_pick_rejects_junk() -> None:
    with pytest.raises(UsageError):
        _parse_pick("a,b")


def test_download_help_documents_id_format(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["download", "--help"]) == 0
    out = capsys.readouterr().out
    assert "id 列" in out
    assert "--ids-file" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["search", "--help"],
        ["search", "bing", "--help"],
        ["search", "ddg", "--help"],
        ["preview", "--help"],
        ["download", "--help"],
        ["montage", "--help"],
    ],
)
def test_help_never_coaches_the_caller(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 的 --help 只描述接口，不写"你该怎么做"——那些话属于 SKILL.md。"""
    assert main(argv) == 0
    out = capsys.readouterr().out
    for phrase in ("调用方", "工具不做", "不要自己拼", "看图上的数字"):
        assert phrase not in out, f"--help 里不该出现流程指导：{phrase!r}"
