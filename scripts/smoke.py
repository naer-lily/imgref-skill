#!/usr/bin/env python3
"""真机 smoke：把一条完整的用户流程跑一遍，人眼可验。

用法::

    python3 scripts/smoke.py <provider> "<query>" [--limit N] [--out DIR] [--download]

它会依次执行：

1. ``search``   —— 真实网络搜索并生成编号拼图；
2. ``preview``  —— 按候选表里的第 1 个 ID 取一张降采样图（验证 ID 自包含）；
3. ``download`` —— 可选：把第 1 个 ID 的全分辨率原图拉下来。

这不是单元测试：它会真的联网。离线测试见 ``tests/``。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imgref.cli import main as cli_main


def main(argv: list[str]) -> int:
    """跑一遍 smoke 流程，返回最后一个命令的退出码。"""
    if len(argv) < 2 or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0 if argv[:1] in ([], ["-h"], ["--help"]) else 1
    provider, query = argv[0], argv[1]
    rest = argv[2:]
    out_root = ".out"
    limit = "12"
    wanted_download = "--download" in rest
    filtered: list[str] = []
    index = 0
    while index < len(rest):
        if rest[index] == "--out" and index + 1 < len(rest):
            out_root = rest[index + 1]
            index += 2
            continue
        if rest[index] == "--limit" and index + 1 < len(rest):
            limit = rest[index + 1]
            index += 2
            continue
        if rest[index] != "--download":
            filtered.append(rest[index])
        index += 1

    print(f"=== 1/3 search: {provider} / {query!r} ===")
    code = cli_main(["search", provider, query, "--out", out_root, "--limit", limit, *filtered])
    if code != 0:
        return code

    manifests = sorted(Path(out_root).rglob("results.json"), key=lambda path: path.stat().st_mtime)
    if not manifests:
        print("没有找到 manifest，无法继续", file=sys.stderr)
        return 1
    manifest = manifests[-1]

    print(f"\n=== 2/3 preview: --pick 1 --from {manifest} ===")
    code = cli_main(["preview", "--pick", "1", "--from", str(manifest), "--max", "1024"])
    if code != 0:
        return code

    if wanted_download:
        print("\n=== 3/3 download: --pick 1 ===")
        code = cli_main(["download", "--pick", "1", "--from", str(manifest), "--out", f"{out_root}/downloads"])
    else:
        print("\n=== 3/3 download: 已跳过（加 --download 打开）===")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
