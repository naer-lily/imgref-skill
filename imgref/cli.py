"""imgref 命令行入口：参数解析、帮助文本、退出码。

帮助文本刻意分成三段，回答"哪些参数是所有图源共用的、哪些是某个图源专属的"：

* ``全局参数（所有命令共用）``
* ``搜索参数（所有图源共用）``
* ``<图源名> 专属参数（来自 <类名>）`` ← 由图源适配器自己的 ``args`` 声明生成

``imgref search <provider> --help`` 会显示后两段；
``imgref search --help`` 会退化成图源索引表。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, NoReturn

from imgref import __version__
from imgref.console import Console, display_width
from imgref.ctx import open_ctx
from imgref.errors import EXIT_EMPTY, EXIT_OK, EXIT_USAGE, ImgrefError, NoResultsError, ProviderError, UsageError
from imgref.grab import GrabRequest, collect_ids, grab
from imgref.grid import GridSpec
from imgref.manifest import pick_ids
from imgref.montage import run_montage
from imgref.providers import PROVIDERS, get_provider, provider_rows
from imgref.providers.base import Provider, parse_options
from imgref.run import SearchRequest, run_search

__all__ = ["main"]

PROG: Final = "imgref"
SKILL_ROOT: Final = Path(__file__).resolve().parent.parent


def default_out_root() -> Path:
    """默认运行目录根：``<skill>/.out``。"""
    return SKILL_ROOT / ".out"


def default_cache_dir() -> Path:
    """默认缓存目录：按平台惯例放用户缓存下。"""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "imgref"


@dataclass(frozen=True, slots=True)
class GlobalOpts:
    """所有命令共用的选项。"""

    cache_dir: Path
    cache_mb: int
    no_cache: bool
    timeout: float
    proxy: str | None
    concurrency: int
    verbose: bool
    quiet: bool


@dataclass(frozen=True, slots=True)
class SearchCmd:
    """``search`` 的解析结果。"""

    provider: Provider
    query: str
    opts: object
    out_root: Path
    keep: int
    limit: int
    label: str | None
    spec: GridSpec
    max_edge: int
    exclude: tuple[Path, ...]
    write_manifest: bool
    g: GlobalOpts


@dataclass(frozen=True, slots=True)
class GrabCmd:
    """``preview`` / ``download`` 的解析结果。"""

    kind: Literal["preview", "download"]
    ids: tuple[str, ...]
    out_dir: Path
    max_edge: int
    g: GlobalOpts


@dataclass(frozen=True, slots=True)
class MontageCmd:
    """``montage`` 的解析结果。"""

    manifests: tuple[Path, ...]
    out_path: Path
    title: str
    spec: GridSpec
    write_json: bool
    g: GlobalOpts


type Command = SearchCmd | GrabCmd | MontageCmd


class _CleanExit(Exception):
    """argparse 正常退出（``-h`` / ``--version``）。"""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class _Parser(argparse.ArgumentParser):
    """把 argparse 的"打印并退出"改成异常，好让退出码统一由 :func:`main` 决定。"""

    def error(self, message: str) -> NoReturn:  # noqa: D102 - 覆盖父类
        raise UsageError(f"{message}\n\n{self.format_help()}")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:  # noqa: D102 - 覆盖父类
        if message:
            sys.stderr.write(message)
        raise _CleanExit(status)


def _root_help() -> str:
    """顶层帮助。"""
    return f"""usage: {PROG} <command> [options]

多图源参考图搜索。一次 search 处理一个图源与一个查询串，产出编号拼图 grid.jpg、
候选表（stdout）与溯源清单 results.json。

commands:
  search <provider> "<query>"    搜索并生成编号拼图与候选表
  preview <id>...                取图到缓存目录，可选降采样
  download <id>... --out DIR     取全分辨率原图
  montage <results.json>...      把多份结果合并成一张拼图（离线，不发请求）

ID: <provider>|<image_url>，自包含，可直接作为 preview / download 的参数。
    候选表的 id 列即完整 ID；拼图上的序号仅供查阅，不是 ID。

退出码: 0 成功（含部分失败，细节见 stdout 的 warn: 行）；1 用法或抓取失败；2 无结果。

示例:
  {PROG} search wikimedia "M1911 pistol"
  {PROG} search bing "M1911 left side" --limit 6 --label side
  {PROG} download --pick 1,3,7 --from <results.json> --out ./refs
  {PROG} montage <a>/results.json <b>/results.json --out merged.jpg

全局: -v 调试信息到 stderr | -q 只输出警告与错误 | --no-cache | --timeout | --proxy
"""


def _provider_index() -> str:
    """图源索引表（用于 ``search --help`` 与未知图源的报错）。"""
    rows = provider_rows()
    width = max(display_width(name) for name, _, _ in rows)
    lines = [f"  {name}{' ' * (width - display_width(name))}  {label}   [私参: {flags}]" for name, label, flags in rows]
    body = "\n".join(lines)
    return (
        "可用图源（provider）:\n"
        f"{body}\n\n"
        f"用 `{PROG} search <provider> --help` 查看某个图源的完整参数。\n"
        "带 ★ 的图源需要额外配置环境变量（见括号里的说明）。"
    )


def _add_global_args(parser: _Parser) -> None:
    """挂上"所有命令共用"的选项。"""
    group = parser.add_argument_group("全局参数（所有命令共用）")
    group.add_argument("--cache-dir", metavar="DIR", help=f"图片缓存目录（默认 {default_cache_dir()}）")
    group.add_argument("--cache-mb", type=int, default=200, metavar="MB", help="缓存容量上限，超出按最旧淘汰（默认 200）")
    group.add_argument("--no-cache", action="store_true", help="禁用图片缓存（只影响快慢，不影响结果）")
    group.add_argument("--timeout", type=float, default=15.0, metavar="SEC", help="单请求超时秒数（默认 15）")
    group.add_argument("--proxy", metavar="URL", help="显式代理；缺省时读 HTTPS_PROXY / HTTP_PROXY / NO_PROXY")
    group.add_argument("--concurrency", type=int, default=8, metavar="N", help="图片下载并发数（默认 8）")
    group.add_argument("-v", "--verbose", action="store_true", help="把内部调试信息打到 stderr")
    group.add_argument("-q", "--quiet", action="store_true", help="不打印常规信息（警告仍然打印）")


def _flags(raw: str | Sequence[str]) -> tuple[str, ...]:
    """把 ``Arg.flags`` 统一成位置参数元组。"""
    return (raw,) if isinstance(raw, str) else tuple(raw)


def _add_provider_args(parser: _Parser, provider: Provider) -> None:
    """把图源自己声明的私有参数挂进独立分组。"""
    if not provider.args:
        return
    group = parser.add_argument_group(f"{provider.name} 专属参数（来自 {type(provider).__name__}）")
    for arg in provider.args:
        kwargs: dict[str, Any] = {"help": arg.help}
        if arg.choices is not None:
            kwargs["choices"] = list(arg.choices)
        if arg.action is not None:
            kwargs["action"] = arg.action
        elif arg.type is not None:
            kwargs["type"] = arg.type
        if arg.default is not None or arg.action not in {"store_true", "store_false"}:
            kwargs["default"] = arg.default
        if arg.metavar is not None:
            kwargs["metavar"] = arg.metavar
        group.add_argument(*_flags(arg.flags), **kwargs)


def _build_search_parser(provider: Provider) -> _Parser:
    """构造 ``search`` 的解析器（已绑定某个图源）。"""
    parser = _Parser(
        prog=f"{PROG} search {provider.name}",
        description=f"用 {provider.name} 搜一次，生成一张编号拼图与一张候选表。",
        epilog=(
            "输出：stdout 依次为 `grid:` 与 `data:` 两个绝对路径、一张候选表（列：序号 / 尺寸 / 来源 / id）。\n"
            "警告（缩略图下载失败、候选超出拼图容量等）同样打印在 stdout 上。\n"
            "运行目录为 <out>/<UTC 时间戳>-<provider>-<slug>/，内含 grid.jpg、results.json 与 thumbs/。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_global_args(parser)
    group = parser.add_argument_group("搜索参数（所有图源共用）")
    group.add_argument("--out", metavar="DIR", help=f"运行目录根；本次结果落在 <DIR>/<时间戳>-{provider.name}-<slug>/（默认 {default_out_root()}）")
    group.add_argument("--keep", type=int, default=10, metavar="N", help="只保留最近 N 个运行目录（默认 10，0=不清理）")
    group.add_argument("--limit", type=int, default=12, metavar="N", help="候选张数上限（默认 12）")
    group.add_argument("--label", metavar="TEXT", help="给这一轮起个标签，写进拼图标题与 manifest")
    group.add_argument("--cols", type=int, default=4, metavar="N", help="拼图列数（默认 4）")
    group.add_argument("--rows", type=int, default=3, metavar="N", help="拼图行数（默认 3）")
    group.add_argument("--cell", type=int, default=375, metavar="PX", help="每格边长（默认 375）")
    group.add_argument("--max-edge", type=int, default=1536, metavar="PX", help="拼图长边上限（默认 1536）")
    group.add_argument("--exclude", action="append", default=[], metavar="FILE", help="按 aHash 排除旧 manifest 里出现过的图（可重复）")
    group.add_argument("--no-json", action="store_true", help="不写 results.json")
    _add_provider_args(parser, provider)
    parser.add_argument("query", metavar='"<query>"', help="查询串")
    return parser


def _add_pick_args(parser: _Parser) -> None:
    """挂上 ``--pick`` / ``--from`` 这组可选糖。"""
    group = parser.add_argument_group("多选快捷方式（可选）")
    group.add_argument("--pick", metavar="1,3,7", help="按候选表里的序号选图；必须配合 --from")
    group.add_argument("--from", dest="from_manifest", metavar="FILE", help="配合 --pick 使用的 results.json")


def _build_grab_parser(kind: Literal["preview", "download"]) -> _Parser:
    """构造 ``preview`` / ``download`` 的解析器（两者共用一份实现）。"""
    if kind == "preview":
        description = "按 ID 取图到缓存目录并可选降采样。"
        default_out = "<缓存目录>/preview"
        max_help = "长边收敛到该值（默认 1024）；0 表示不降采样"
    else:
        description = "按 ID 取全分辨率原图到指定目录。"
        default_out = "<skill>/.out/downloads"
        max_help = "长边收敛到该值；默认 0 = 保留原始字节（原图）"
    parser = _Parser(
        prog=f"{PROG} {kind}",
        description=description,
        epilog=(
            "ID 形如 `wikimedia|https://upload.wikimedia.org/.../a.png`，即 search 输出候选表里的 id 列。\n"
            "多个 ID 可并列给出，或用 --ids-file（每行一个），或用 --pick 配合 --from。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ids", nargs="*", metavar="<id>", help="自包含 ID，可给多个")
    _add_global_args(parser)
    group = parser.add_argument_group(f"{kind} 参数")
    group.add_argument("--out", metavar="DIR", help=f"输出目录（默认 {default_out}）")
    group.add_argument("--max", type=int, default=1024 if kind == "preview" else 0, metavar="PX", help=max_help)
    group.add_argument("--ids-file", metavar="FILE", help="每行一个 ID 的文件（ID 太长、命令行塞不下时用）")
    _add_pick_args(parser)
    return parser


def _build_montage_parser() -> _Parser:
    """构造 ``montage`` 的解析器。"""
    parser = _Parser(
        prog=f"{PROG} montage",
        description="把多轮 search 的编号拼图合并成一张（连续重新编号）。不发网络请求，直接用各轮已落盘的缩略图。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("manifests", nargs="+", metavar="<results.json>", help="各轮的 results.json，按给定顺序合并")
    _add_global_args(parser)
    group = parser.add_argument_group("montage 参数")
    group.add_argument("--out", required=True, metavar="FILE", help="输出拼图路径（例如 merged.jpg）")
    group.add_argument("--title", default="", metavar="TEXT", help="顶部标题（只会保留 ASCII 部分）")
    group.add_argument("--cols", type=int, default=4, metavar="N", help="列数（默认 4）")
    group.add_argument("--rows", type=int, default=3, metavar="N", help="行数（默认 3）")
    group.add_argument("--cell", type=int, default=375, metavar="PX", help="每格边长（默认 375）")
    group.add_argument("--no-json", action="store_true", help="不写 merged.json")
    return parser


def _global_opts(ns: argparse.Namespace) -> GlobalOpts:
    """从解析结果里抠出全局选项。"""
    cache_dir = Path(ns.cache_dir).expanduser() if ns.cache_dir else default_cache_dir()
    return GlobalOpts(
        cache_dir=cache_dir,
        cache_mb=max(int(ns.cache_mb), 1),
        no_cache=bool(ns.no_cache),
        timeout=float(ns.timeout),
        proxy=str(ns.proxy) if ns.proxy else None,
        concurrency=max(int(ns.concurrency), 1),
        verbose=bool(ns.verbose),
        quiet=bool(ns.quiet),
    )


def _parse_pick(raw: str | None) -> tuple[int, ...]:
    """解析 ``--pick 1,3,7``。"""
    if not raw:
        return ()
    picked: list[int] = []
    for chunk in raw.replace("，", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            picked.append(int(item))
        except ValueError:
            raise UsageError(f"--pick 只接受逗号分隔的序号，收到 {item!r}") from None
    return tuple(picked)


def _parse_search(argv: list[str]) -> SearchCmd:
    """解析 ``search`` 子命令（两段式：先认图源，再建带私参的解析器）。"""
    if not argv:
        raise UsageError(f"search 缺少图源名。\n\n{_provider_index()}")
    if argv[0] in {"-h", "--help"}:
        raise _CleanExit(_show(_provider_index()))
    provider = get_provider(argv[0])
    parser = _build_search_parser(provider)
    ns = parser.parse_args(argv[1:])
    spec = GridSpec(cols=max(int(ns.cols), 1), rows=max(int(ns.rows), 1), cell=max(int(ns.cell), 64)).fit_max_edge(int(ns.max_edge))
    return SearchCmd(
        provider=provider,
        query=str(ns.query),
        opts=parse_options(provider, ns),
        out_root=Path(ns.out).expanduser() if ns.out else default_out_root(),
        keep=int(ns.keep),
        limit=max(int(ns.limit), 1),
        label=str(ns.label) if ns.label else None,
        spec=spec,
        max_edge=int(ns.max_edge),
        exclude=tuple(Path(item).expanduser() for item in ns.exclude),
        write_manifest=not bool(ns.no_json),
        g=_global_opts(ns),
    )


def _parse_grab(kind: Literal["preview", "download"], argv: list[str]) -> GrabCmd:
    """解析 ``preview`` / ``download``。"""
    ns = _build_grab_parser(kind).parse_args(argv)
    manifest = Path(ns.from_manifest).expanduser() if ns.from_manifest else None
    picked = _parse_pick(ns.pick)
    if picked and manifest is None:
        raise UsageError("--pick 必须配合 --from <results.json> 才能确定序号对应的图")
    from_pick = pick_ids(picked, manifest) if picked and manifest is not None else []
    ids = collect_ids(
        tuple(ns.ids),
        ids_file=Path(ns.ids_file).expanduser() if ns.ids_file else None,
        pick=from_pick,
    )
    if not ids:
        raise UsageError("没有给任何 ID；用法示例：imgref download 'bing|https://…' --out ./refs")
    default_out = default_cache_dir() / "preview" if kind == "preview" else default_out_root() / "downloads"
    return GrabCmd(
        kind=kind,
        ids=tuple(ids),
        out_dir=Path(ns.out).expanduser() if ns.out else default_out,
        max_edge=max(int(ns.max), 0),
        g=_global_opts(ns),
    )


def _parse_montage(argv: list[str]) -> MontageCmd:
    """解析 ``montage``。"""
    ns = _build_montage_parser().parse_args(argv)
    return MontageCmd(
        manifests=tuple(Path(item).expanduser() for item in ns.manifests),
        out_path=Path(ns.out).expanduser(),
        title=str(ns.title),
        spec=GridSpec(cols=max(int(ns.cols), 1), rows=max(int(ns.rows), 1), cell=max(int(ns.cell), 64)),
        write_json=not bool(ns.no_json),
        g=_global_opts(ns),
    )


def _parse(argv: Sequence[str]) -> Command:
    """把命令行解析成命令对象（帮助与用法错误都通过异常返回）。"""
    if not argv or argv[0] in {"-h", "--help"}:
        raise _CleanExit(_show(_root_help()))
    if argv[0] == "--version":
        raise _CleanExit(_show(f"{PROG} {__version__}"))
    command, rest = argv[0], list(argv[1:])
    match command:
        case "search":
            return _parse_search(rest)
        case "preview":
            return _parse_grab("preview", rest)
        case "download":
            return _parse_grab("download", rest)
        case "montage":
            return _parse_montage(rest)
        case _:
            raise UsageError(f"未知命令 {command!r}\n\n{_root_help()}")


def _show(text: str) -> int:
    """打印帮助并返回 0。"""
    print(text)
    return EXIT_OK


async def _execute(cmd: Command, console: Console) -> int:
    """按命令类型分派。"""
    match cmd:
        case SearchCmd():
            return await _do_search(cmd, console)
        case GrabCmd():
            return await _do_grab(cmd, console)
        case MontageCmd():
            return _do_montage(cmd, console)


async def _do_search(cmd: SearchCmd, console: Console) -> int:
    """执行一次 ``search``。"""
    g = cmd.g
    console.debug(f"图源={cmd.provider.name} 私有参数={cmd.opts!r} 版式={cmd.spec!r}")
    async with open_ctx(
        label=cmd.provider.name,
        timeout=g.timeout,
        proxy=g.proxy,
        cache_dir=g.cache_dir,
        cache_mb=g.cache_mb,
        no_cache=g.no_cache,
        concurrency=g.concurrency,
    ) as ctx:
        outcome = await run_search(
            ctx,
            SearchRequest(
                provider=cmd.provider,
                query=cmd.query,
                opts=cmd.opts,
                limit=cmd.limit,
                spec=cmd.spec,
                out_root=cmd.out_root,
                keep=cmd.keep,
                exclude=cmd.exclude,
                label=cmd.label,
                write_manifest=cmd.write_manifest,
            ),
        )
    if not g.quiet:
        console.out(
            f"[{cmd.provider.name}] {cmd.query!r} | 候选 {outcome.candidates} → {len(outcome.rows)} 格"
            f" | 去重排除 {outcome.dropped} | {outcome.elapsed:.1f}s"
        )
    console.out(f"grid: {outcome.grid_path.resolve()}")
    if outcome.manifest_path is not None:
        console.out(f"data: {outcome.manifest_path.resolve()}")
    console.out("")
    width = max((len(row.size) for row in outcome.rows), default=4)
    source_width = max((len(row.source) for row in outcome.rows), default=6)
    console.out(f"{'#':>3}  {'size'.ljust(width)}  {'source'.ljust(source_width)}  id")
    for row in outcome.rows:
        console.out(f"{row.ordinal:>3}  {row.size.ljust(width)}  {row.source.ljust(source_width)}  {row.ref_id}")
    for warning in outcome.warnings:
        console.warn(warning)
    return EXIT_OK


async def _do_grab(cmd: GrabCmd, console: Console) -> int:
    """执行 ``preview`` / ``download``。"""
    g = cmd.g
    async with open_ctx(
        label="-",
        timeout=g.timeout,
        proxy=g.proxy,
        cache_dir=g.cache_dir,
        cache_mb=g.cache_mb,
        no_cache=g.no_cache,
        concurrency=g.concurrency,
    ) as ctx:
        files, warnings = await grab(ctx, GrabRequest(ids=cmd.ids, out_dir=cmd.out_dir, max_edge=cmd.max_edge), PROVIDERS)
    for item in files:
        note = f"  ({item.source_size} → 降采样)" if item.downscaled else ""
        console.out(f"{item.path.resolve()}{note}")
    for warning in warnings:
        console.warn(warning)
    if not files:
        console.error(f"{cmd.kind} 失败：{len(cmd.ids)} 个 ID 一个都没取到")
        return EXIT_USAGE
    if not g.quiet:
        console.out(f"{cmd.kind}: {len(files)}/{len(cmd.ids)} 张 → {cmd.out_dir.resolve()}")
    return EXIT_OK


def _do_montage(cmd: MontageCmd, console: Console) -> int:
    """执行 ``montage``。"""
    outcome = run_montage(cmd.manifests, cmd.out_path, title=cmd.title, spec=cmd.spec, write_json=cmd.write_json)
    console.out(f"grid: {outcome.grid_path.resolve()}")
    if outcome.manifest_path is not None:
        console.out(f"data: {outcome.manifest_path.resolve()}")
    for warning in outcome.warnings:
        console.warn(warning)
    if not cmd.g.quiet:
        console.out(f"montage: {len(outcome.cells)} 格 ← {len(cmd.manifests)} 份 manifest")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。

    Args:
        argv: 参数列表；``None`` 时取 ``sys.argv[1:]``。

    Returns:
        进程退出码：0 成功、1 用法/抓取失败、2 一个结果都没有。
    """
    console = Console()
    try:
        cmd = _parse(list(sys.argv[1:] if argv is None else argv))
    except _CleanExit as exc:
        return exc.status
    except UsageError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    console = Console(quiet=cmd.g.quiet, verbose=cmd.g.verbose)
    try:
        return asyncio.run(_execute(cmd, console))
    except KeyboardInterrupt:
        console.error("已中断")
        return 130
    except ProviderError as exc:
        console.error(str(exc))
        hint = exc.hint()
        if hint:
            console.error(hint)
        return EXIT_USAGE
    except NoResultsError as exc:
        console.error(str(exc))
        return EXIT_EMPTY
    except ImgrefError as exc:
        console.error(str(exc))
        return exc.exit_code
