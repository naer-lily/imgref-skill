#!/usr/bin/env python3
"""imgref 入口：先自举虚拟环境，再把控制权交给真正的 CLI。

这个文件的**前半部分只用标准库**，因为它在依赖就绪之前运行。自举策略对调用方完全透明：

1. 用 ``__file__`` 定位技能根目录——绝不依赖 cwd，从哪个目录跑都一样；
2. ``requirements.txt`` 的 sha256 存成 stamp：**只有依赖真的变了才重装**，
   不是"venv 存在就跳过"；
3. 建环境加锁（``O_CREAT|O_EXCL``），并发调用不会把 venv 建坏；
4. 装依赖用 ``--only-binary=:all:``，不在小内存服务器上触发本地编译；
5. POSIX 用 ``os.execv`` 真替换当前进程（内存不叠加，毫秒级），
   Windows 用子进程 + 原样退出码（``exec*`` 在 Windows 有句柄继承的历史坑）；
6. 子进程带 ``IMGREF_BOOTSTRAPPED=1``，依赖装不上时也不会无限自举；
7. 跳转前 ``flush`` 两个流，否则前面打印的内容会丢。

依赖全部落在技能目录自己的 ``.venv`` 里，全局环境零污染。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
REQS = ROOT / "requirements.txt"
STAMP = VENV / ".reqstamp"
LOCK = ROOT / ".venv.lock"
GUARD = "IMGREF_BOOTSTRAPPED"
LOCK_TIMEOUT = 300.0
ENTRY = Path(__file__).resolve()
REQUIRED_PYTHON = (3, 13)


def _check_python() -> str | None:
    """版本不够就返回一句可操作的报错（而不是让人看到 PEP 695 的语法错误）。"""
    if sys.version_info < REQUIRED_PYTHON:
        wanted = ".".join(str(part) for part in REQUIRED_PYTHON)
        return f"需要 Python {wanted}+，当前是 {sys.version.split()[0]}（{sys.executable}）"
    return None


def _venv_python_for(root: Path, *, nt: bool) -> Path:
    """按平台拼出虚拟环境解释器路径（抽出来是为了两个平台都能测到）。"""
    venv = root / ".venv"
    if nt:
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python3"


def _venv_python() -> Path:
    """当前平台的虚拟环境解释器路径。"""
    return _venv_python_for(ROOT, nt=os.name == "nt")


def _requirements_hash() -> str:
    """``requirements.txt`` 的 sha256；读不到就返回空串。"""
    try:
        return hashlib.sha256(REQS.read_bytes()).hexdigest()
    except OSError:
        return ""


def _stamp_matches() -> bool:
    """依赖是否与上次安装时一致。"""
    try:
        return STAMP.read_text(encoding="utf-8").strip() == _requirements_hash()
    except OSError:
        return False


class _Lock:
    """跨平台的目录锁：抢不到就等，等到超时也只是继续（宁可慢，不要死）。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> _Lock:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    print("[imgref] 等待环境锁超时，改用现有解释器继续", file=sys.stderr)
                    return self
                time.sleep(0.5)

    def __exit__(self, *_: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._path.unlink()
        except OSError:
            pass


def _install(python: Path) -> None:
    """建虚拟环境并安装依赖（幂等：依赖没变时会被 stamp 挡住，不会走到这里）。"""
    fresh = not _venv_python().exists()
    print(f"[imgref] {'首次运行：正在创建 .venv 并安装依赖' if fresh else '依赖有变动：正在更新 .venv'}（只需一次）…", file=sys.stderr)
    if fresh:
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--only-binary=:all:",
            "--disable-pip-version-check",
            "-r",
            str(REQS),
        ],
        check=True,
    )
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(_requirements_hash(), encoding="utf-8")


def _ensure_env() -> Path | None:
    """确保虚拟环境可用，返回它的解释器路径；失败返回 ``None``。"""
    problem = _check_python()
    if problem is not None:
        print(f"[imgref] {problem}", file=sys.stderr)
        return None
    python = _venv_python()
    if python.exists() and _stamp_matches():
        return python
    with _Lock(LOCK):
        python = _venv_python()
        if python.exists() and _stamp_matches():
            return python
        try:
            _install(python)
        except subprocess.CalledProcessError as exc:
            print(
                f"[imgref] 依赖安装失败（{exc}）。\n"
                "  常见原因：系统缺少 venv/pip（Debian/Ubuntu 需 `apt install python3-venv`），\n"
                "  或 Python 是 Microsoft Store 的占位程序（请改用官方安装的 Python）。",
                file=sys.stderr,
            )
            return None
        return _venv_python() if _venv_python().exists() else None


def _reexec(python: Path, env: dict[str, str], *, nt: bool | None = None) -> int:
    """用虚拟环境的解释器重跑自己。

    POSIX 上 :func:`os.execve` 会**替换**当前进程，所以不会出现父子两份 Python；
    Windows 上改用子进程并原样返回退出码（``exec*`` 在 Windows 有句柄继承的历史坑）。
    """
    argv = [str(python), str(ENTRY), *sys.argv[1:]]
    if os.name == "nt" if nt is None else nt:
        return subprocess.run(argv, env=env).returncode
    os.execve(str(python), argv, env)


def main() -> int:
    """自举后调用真正的 CLI。"""
    if os.environ.get(GUARD) == "1":
        sys.path.insert(0, str(ROOT))
        from imgref.cli import main as cli_main

        return cli_main(sys.argv[1:])
    python = _ensure_env()
    if python is None:
        return 1
    sys.stdout.flush()
    sys.stderr.flush()
    env = {**os.environ, GUARD: "1"}
    return _reexec(python, env)


if __name__ == "__main__":
    raise SystemExit(main())
