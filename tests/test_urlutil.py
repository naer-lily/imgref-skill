"""URL 归一化与域名提取测试。"""

from __future__ import annotations

import pytest

from imgref.urlutil import domain_of, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP://WWW.Example.com/a/b.jpg?w=300&utm_source=x#frag", "https://example.com/a/b.jpg"),
        ("https://example.com/a/b.jpg", "https://example.com/a/b.jpg"),
        ("http://example.com/a/b.jpg", "https://example.com/a/b.jpg"),
        ("https://example.com/a/b.jpg?b=2&a=1", "https://example.com/a/b.jpg?a=1&b=2"),
        ("https://example.com/a/b.jpg?q=90&quality=80", "https://example.com/a/b.jpg"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("", ""),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_normalize_url_keeps_meaningful_params() -> None:
    normalized = normalize_url("https://example.com/i?id=42")
    assert normalized == "https://example.com/i?id=42"


def test_normalize_url_returns_input_when_unparseable() -> None:
    assert normalize_url("not a url") == "not a url"
    assert normalize_url("  ") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.x.com/p", "x.com"),
        ("https://upload.wikimedia.org/wikipedia/a.jpg", "upload.wikimedia.org"),
        ("http://x.com:8080/p", "x.com"),
        (None, ""),
        ("", ""),
        ("nonsense", ""),
    ],
)
def test_domain_of(raw: str | None, expected: str) -> None:
    assert domain_of(raw) == expected
