"""图源协议与注册表测试。

这里检查的是"扩展一个图源"的契约：只要形状对，不需要继承任何东西。
"""

from __future__ import annotations

import argparse

import pytest

from imgref.errors import UsageError
from imgref.providers import PROVIDERS, get_provider, provider_rows
from imgref.providers.base import (
    ProvidesHeaders,
    as_int,
    format_tag_problems,
    headers_for,
    is_plain_tag,
    missing_env,
    parse_options,
)
from imgref.types import Arg
from tests.helpers import FakeProvider


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("landscape", True),
        ("cat_ears", True),
        ("hatsune_miku_(cosplay)", True),
        ("rating:s", False),
        ("order:score", False),
        ("score:>10", False),
        ("-cat", False),
        ("cat*", False),
        ("~cat", False),
        ("", False),
    ],
)
def test_is_plain_tag(tag: str, expected: bool) -> None:
    """只有普通标签才该拿去查标签表；元语法/通配/排除交给图源自己理解。"""
    assert is_plain_tag(tag) is expected


def test_format_tag_problems() -> None:
    text = format_tag_problems("yande.re", ["no_humans", "scenery"], {"no_humans": ["no_bra", "no_hat"]})
    assert text == "yande.re 上没有这些标签：no_humans（相近：no_bra、no_hat）；scenery（没有相近的）"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (804, 804),
        ("804", 804),
        (" 804 ", 804),
        ("-3", -3),
        (0, 0),
        (True, None),
        (False, None),
        (8.5, None),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_as_int(value: object, expected: int | None) -> None:
    """真实接口的数字字段类型很随意（堆糖的宽高就是字符串）。"""
    assert as_int(value) == expected


def test_registry_is_populated_and_consistent() -> None:
    assert PROVIDERS
    for name, provider in PROVIDERS.items():
        assert provider.name == name
        assert provider.label
        assert isinstance(provider.args, (tuple, list))
        assert all(isinstance(arg, Arg) for arg in provider.args)


def test_registry_covers_expected_sources() -> None:
    assert set(PROVIDERS) == {"bing", "ddg", "wikimedia", "openverse", "yandere", "safebooru", "duitang", "serper"}


def test_get_provider_unknown_lists_alternatives() -> None:
    with pytest.raises(UsageError) as info:
        get_provider("google")
    assert "bing" in str(info.value)


def test_provider_rows_sorted_and_flagged() -> None:
    rows = provider_rows()
    names = [row[0] for row in rows]
    assert names == sorted(names)
    labels = {row[0]: row[1] for row in rows}
    assert "SERPER_API_KEY" in labels["serper"]
    assert "★" in labels["serper"]


def test_provider_rows_show_private_args() -> None:
    rows = {row[0]: row[2] for row in provider_rows()}
    assert "--safe" in rows["bing"]
    assert "--region" in rows["ddg"]
    assert rows["bing"] != rows["ddg"]


def test_missing_env_reports_unset_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert missing_env(PROVIDERS["serper"]) == ["SERPER_API_KEY"]
    monkeypatch.setenv("SERPER_API_KEY", "k")
    assert missing_env(PROVIDERS["serper"]) == []
    assert missing_env(PROVIDERS["bing"]) == []


def test_headers_for_uses_optional_protocol() -> None:
    bing = PROVIDERS["bing"]
    assert isinstance(bing, ProvidesHeaders)
    assert "Referer" in headers_for(bing, "https://x.com/a.jpg")
    assert headers_for(FakeProvider([]), "https://x.com/a.jpg") == {}


def test_parse_options_is_optional() -> None:
    ns = argparse.Namespace(safe="strict")
    options = parse_options(PROVIDERS["bing"], ns)
    assert getattr(options, "safe") == "strict"
    assert parse_options(FakeProvider([]), ns) is None


def test_provider_shape_is_structural() -> None:
    """协议是结构性的：不需要继承，只要成员齐备。

    刻意不用 ``isinstance(x, Provider)``——带数据成员的 runtime_checkable 协议
    在运行时的行为不值得依赖，产品代码里也从不这么做（静态检查已经覆盖）。
    """
    for candidate in (FakeProvider([[]]), PROVIDERS["bing"]):
        for member in ("name", "label", "args", "requires", "search"):
            assert hasattr(candidate, member)
        assert callable(candidate.search)
