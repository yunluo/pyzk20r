# -*- coding: utf-8 -*-
"""版本号一致性测试: pyproject.toml / __init__.py / uv.lock / git tag 四处不得漂移。"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "version.py"


def _load_tool():
    """按文件路径加载 scripts/version.py (避免污染 sys.path)。"""
    spec = importlib.util.spec_from_file_location("version_tool", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version_tool = _load_tool()


# --------------------------------------------------------------------------
# 仓库自身
# --------------------------------------------------------------------------


def test_version_sources_agree():
    """pyproject.toml / __init__.py / uv.lock 三处版本号必须一致。"""
    pyproject_version = version_tool.read_pyproject_version()
    assert version_tool.read_init_version() == pyproject_version
    if version_tool.UV_LOCK.exists():
        assert version_tool.read_lock_version() == pyproject_version


def test_check_command_passes():
    """check 子命令在本仓库必须通过 (CI 的 version job 跑的就是这条)。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "check"],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, output


# --------------------------------------------------------------------------
# 漂移检测 (临时仓库)
# --------------------------------------------------------------------------


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=test"] + list(args),
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _write_sources(root, version):
    """在 root 下写出一份最小可用的版本文件。"""
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pyzk20r"\nversion = "{}"\n'.format(version), encoding="utf-8"
    )
    pkg = root / "src" / "pyzk20r"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text('__version__ = "{}"\n'.format(version), encoding="utf-8")
    (root / "uv.lock").write_text(
        '[[package]]\nname = "pyzk20r"\nversion = "{}"\nsource = {{ editable = "." }}\n'.format(
            version
        ),
        encoding="utf-8",
    )


def _run_check(root):
    """在指定根目录跑 check, 返回 (退出码, 输出)。"""
    env = dict(os.environ, PYZK20R_ROOT=str(root))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "check"],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


@pytest.fixture
def fake_repo():
    """一个已提交且打了正确 tag 的临时仓库。

    不用 pytest 的 tmp_path: pytest 7.4 在 Windows 上回收临时目录时会抛
    "embedded null character" 异常 (退出时的 atexit 报错), 这里自管清理。
    """
    if shutil.which("git") is None:
        pytest.skip("需要 git 才能测试 tag 漂移检测")
    root = Path(tempfile.mkdtemp(prefix="pyzk20r-version-"))
    try:
        _write_sources(root, "0.9.0")
        _git(root, "init")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", "init")
        _git(root, "tag", "-a", "v0.9.0", "-m", "v0.9.0")
        yield root
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_check_passes_on_consistent_repo(fake_repo):
    code, out = _run_check(fake_repo)
    assert code == 0, out


def test_check_detects_tag_drift(fake_repo):
    """tag 指向的提交里版本号对不上 -> 必须报错。"""
    _git(fake_repo, "tag", "-a", "v0.9.1", "-m", "v0.9.1")
    code, out = _run_check(fake_repo)
    assert code == 1
    assert "版本漂移" in out


def test_check_detects_init_mismatch(fake_repo):
    """__init__.py 的 __version__ 落后 -> 必须报错。"""
    (fake_repo / "src" / "pyzk20r" / "__init__.py").write_text(
        '__version__ = "0.8.0"\n', encoding="utf-8"
    )
    code, out = _run_check(fake_repo)
    assert code == 1
    assert "__version__" in out


def test_check_detects_lock_mismatch(fake_repo):
    """uv.lock 里的版本落后 -> 必须报错。"""
    (fake_repo / "uv.lock").write_text(
        '[[package]]\nname = "pyzk20r"\nversion = "0.8.0"\nsource = {{ editable = "." }}\n',
        encoding="utf-8",
    )
    code, out = _run_check(fake_repo)
    assert code == 1
    assert "uv.lock" in out


def test_check_detects_version_behind_tag(fake_repo):
    """toml 版本低于已打 tag -> 必须报错。"""
    _write_sources(fake_repo, "0.8.0")
    code, out = _run_check(fake_repo)
    assert code == 1
    assert "版本漂移" in out


def test_bump_syncs_all_sources(fake_repo):
    """bump 要同时改 pyproject / __init__ / uv.lock 三处。"""
    env = dict(os.environ, PYZK20R_ROOT=str(fake_repo))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "bump", "0.9.1"],
        cwd=str(fake_repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
    text = (fake_repo / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.9.1"' in text
    init_text = (fake_repo / "src" / "pyzk20r" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "0.9.1"' in init_text
    lock_text = (fake_repo / "uv.lock").read_text(encoding="utf-8")
    assert 'version = "0.9.1"' in lock_text


def test_bump_refuses_downgrade(fake_repo):
    env = dict(os.environ, PYZK20R_ROOT=str(fake_repo))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "bump", "0.8.0"],
        cwd=str(fake_repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.returncode == 1
    assert "不允许降级" in proc.stdout.decode("utf-8", "replace")
