"""venv 自举测试：stamp、锁、平台路径、护栏、失败提示。"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parent.parent / "scripts" / "imgref.py"


@pytest.fixture(scope="module")
def entry() -> types.ModuleType:
    """按文件路径加载入口脚本（不能按模块名导入：那会盖掉 imgref 包）。"""
    spec = importlib.util.spec_from_file_location("imgref_entry", ENTRY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["imgref_entry"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sandbox(entry: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把入口脚本的所有路径常量指向临时目录。"""
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    monkeypatch.setattr(entry, "VENV", tmp_path / ".venv")
    monkeypatch.setattr(entry, "REQS", tmp_path / "requirements.txt")
    monkeypatch.setattr(entry, "STAMP", tmp_path / ".venv" / ".reqstamp")
    monkeypatch.setattr(entry, "LOCK", tmp_path / ".venv.lock")
    (tmp_path / "requirements.txt").write_text("httpx\n", encoding="utf-8")
    return tmp_path


def _python_path(entry: types.ModuleType, root: Path) -> Path:
    """当前平台上虚拟环境解释器的应然路径。"""
    return Path(entry._venv_python_for(root, nt=os.name == "nt"))


def _make_venv(entry: types.ModuleType, root: Path) -> Path:
    """造一个假的 venv 解释器文件。"""
    python = _python_path(entry, root)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"fake")
    return python


def test_check_python_accepts_current(entry: types.ModuleType) -> None:
    assert entry._check_python() is None


def test_check_python_rejects_old(entry: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(entry, "REQUIRED_PYTHON", (99, 0))
    assert entry._ensure_env() is None
    assert "需要 Python 99.0+" in capsys.readouterr().err


def test_venv_python_path_per_platform(entry: types.ModuleType) -> None:
    assert entry._venv_python_for(Path("/x"), nt=True).as_posix() == "/x/.venv/Scripts/python.exe"
    assert entry._venv_python_for(Path("/x"), nt=False).as_posix() == "/x/.venv/bin/python3"


def test_requirements_hash(entry: types.ModuleType, sandbox: Path) -> None:
    expected = hashlib.sha256((sandbox / "requirements.txt").read_bytes()).hexdigest()
    assert entry._requirements_hash() == expected


def test_requirements_hash_missing_file(entry: types.ModuleType, sandbox: Path) -> None:
    (sandbox / "requirements.txt").unlink()
    assert entry._requirements_hash() == ""


def test_stamp_matching(entry: types.ModuleType, sandbox: Path) -> None:
    assert entry._stamp_matches() is False
    entry.STAMP.parent.mkdir(parents=True, exist_ok=True)
    entry.STAMP.write_text("stale", encoding="utf-8")
    assert entry._stamp_matches() is False
    entry.STAMP.write_text(entry._requirements_hash(), encoding="utf-8")
    assert entry._stamp_matches() is True


def test_lock_creates_and_removes(entry: types.ModuleType, sandbox: Path) -> None:
    with entry._Lock(sandbox / ".venv.lock"):
        assert (sandbox / ".venv.lock").exists()
    assert not (sandbox / ".venv.lock").exists()


def test_lock_times_out_and_continues(entry: types.ModuleType, sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(entry, "LOCK_TIMEOUT", 0.1)
    held = sandbox / ".venv.lock"
    held.write_text("held", encoding="utf-8")
    with entry._Lock(held):
        pass
    assert "等待环境锁超时" in capsys.readouterr().err


def test_ensure_env_uses_fast_path_when_stamp_matches(entry: types.ModuleType, sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = _make_venv(entry, sandbox)
    entry.STAMP.parent.mkdir(parents=True, exist_ok=True)
    entry.STAMP.write_text(entry._requirements_hash(), encoding="utf-8")

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("stamp 命中时不该重新安装")

    monkeypatch.setattr(entry, "_install", explode)
    assert entry._ensure_env() == python


def test_ensure_env_installs_when_stamp_missing(entry: types.ModuleType, sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []

    def fake_install(python: Path) -> None:
        calls.append(python)
        _make_venv(entry, sandbox)
        entry.STAMP.parent.mkdir(parents=True, exist_ok=True)
        entry.STAMP.write_text(entry._requirements_hash(), encoding="utf-8")

    monkeypatch.setattr(entry, "_install", fake_install)
    assert entry._ensure_env() == _python_path(entry, sandbox)
    assert len(calls) == 1


def test_ensure_env_reports_install_failure(entry: types.ModuleType, sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def boom(*_: object, **__: object) -> None:
        raise subprocess.CalledProcessError(1, ["pip"])

    monkeypatch.setattr(entry.subprocess, "run", boom)
    assert entry._ensure_env() is None
    assert "依赖安装失败" in capsys.readouterr().err


def test_reexec_on_windows_uses_subprocess(entry: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class Result:
        returncode = 7

    def fake_run(argv: list[str], env: dict[str, str]) -> Result:
        seen["argv"] = argv
        seen["env"] = env
        return Result()

    monkeypatch.setattr(entry.subprocess, "run", fake_run)
    monkeypatch.setattr(entry.sys, "argv", ["imgref.py", "search", "bing", "q"])
    code = entry._reexec(Path("/py"), {"IMGREF_BOOTSTRAPPED": "1"}, nt=True)
    assert code == 7
    assert seen["argv"] == [str(Path("/py")), str(entry.ENTRY), "search", "bing", "q"]
    assert seen["env"] == {"IMGREF_BOOTSTRAPPED": "1"}


def test_main_delegates_when_guarded(entry: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(entry.GUARD, "1")
    monkeypatch.setattr(entry.sys, "argv", ["imgref.py", "--version"])
    assert entry.main() == 0
    assert capsys.readouterr().out.startswith("imgref ")


def test_main_bootstraps_then_reexecs(entry: types.ModuleType, sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(entry.GUARD, raising=False)
    monkeypatch.setattr(entry, "_ensure_env", lambda: Path("/py"))
    seen: dict[str, object] = {}

    def fake_reexec(python: Path, env: dict[str, str]) -> int:
        seen["python"] = python
        seen["env"] = env
        return 3

    monkeypatch.setattr(entry, "_reexec", fake_reexec)
    assert entry.main() == 3
    assert seen["python"] == Path("/py")
    assert isinstance(seen["env"], dict)
    assert seen["env"][entry.GUARD] == "1"


def test_main_returns_1_when_env_unavailable(entry: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(entry.GUARD, raising=False)
    monkeypatch.setattr(entry, "_ensure_env", lambda: None)
    assert entry.main() == 1
