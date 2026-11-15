# -*- coding: utf-8 -*-
"""版本号一致性测试: pyproject.toml / __init__.py / uv.lock / git tag 四处不得漂移。"""

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_DIR, "scripts", "version.py")


def _load_tool():
    """按文件路径加载 scripts/version.py (避免污染 sys.path)。"""
    spec = importlib.util.spec_from_file_location("version_tool", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version_tool = _load_tool()


def _rmtree_readonly(path):
    """删除目录, 遇到只读文件 (git 对象) 先解除只读再删。"""

    def onerror(func, p, _exc_info):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=onerror)


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=test"] + list(args),
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _write_sources(root, version):
    """在 root 下写出一份最小可用的版本文件。"""
    with open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8", newline="\n") as f:
        f.write('[project]\nname = "pyzk20r"\nversion = "{}"\n'.format(version))
    pkg = os.path.join(root, "src", "pyzk20r")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write('__version__ = "{}"\n'.format(version))
    with open(os.path.join(root, "uv.lock"), "w", encoding="utf-8", newline="\n") as f:
        f.write(
            '[[package]]\nname = "pyzk20r"\nversion = "{}"\nsource = {{ editable = "." }}\n'.format(
                version
            )
        )


def _run_check(root):
    """在指定根目录跑 check, 返回 (退出码, 输出)。"""
    env = dict(os.environ, PYZK20R_ROOT=os.path.abspath(root))
    proc = subprocess.run(
        [sys.executable, SCRIPT, "check"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _run_tool(root, *args):
    """在指定根目录跑 version.py 子命令, 返回 CompletedProcess。"""
    env = dict(os.environ, PYZK20R_ROOT=os.path.abspath(root))
    return subprocess.run(
        [sys.executable, SCRIPT] + list(args),
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class TestRepoVersionConsistency(unittest.TestCase):
    """仓库自身的版本一致性 (无临时仓库)。"""

    def test_version_sources_agree(self):
        """pyproject.toml / __init__.py / uv.lock 三处版本号必须一致。"""
        pyproject_version = version_tool.read_pyproject_version()
        self.assertEqual(version_tool.read_init_version(), pyproject_version)
        if version_tool.UV_LOCK.exists():
            self.assertEqual(version_tool.read_lock_version(), pyproject_version)

    def test_check_command_passes(self):
        """check 子命令在本仓库必须通过 (CI 的 version job 跑的就是这条)。"""
        proc = subprocess.run(
            [sys.executable, SCRIPT, "check"],
            cwd=REPO_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, output)


@unittest.skipUnless(shutil.which("git"), "需要 git 才能测试 tag 漂移检测")
class TestDriftDetection(unittest.TestCase):
    """在临时 git 仓库里构造漂移, 验证 check/bump 真的能拦下。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pyzk20r-version-")
        _write_sources(self.root, "0.9.0")
        _git(self.root, "init")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-m", "init")
        _git(self.root, "tag", "-a", "v0.9.0", "-m", "v0.9.0")

    def tearDown(self):
        _rmtree_readonly(self.root)

    def test_check_passes_on_consistent_repo(self):
        code, out = _run_check(self.root)
        self.assertEqual(code, 0, out)

    def test_check_detects_tag_drift(self):
        """tag 指向的提交里版本号对不上 -> 必须报错。"""
        _git(self.root, "tag", "-a", "v0.9.1", "-m", "v0.9.1")
        code, out = _run_check(self.root)
        self.assertEqual(code, 1)
        self.assertIn("版本漂移", out)

    def test_check_detects_init_mismatch(self):
        """__init__.py 的 __version__ 落后 -> 必须报错。"""
        _write_sources(self.root, "0.9.0")
        with open(
            os.path.join(self.root, "src", "pyzk20r", "__init__.py"),
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:
            f.write('__version__ = "0.8.0"\n')
        code, out = _run_check(self.root)
        self.assertEqual(code, 1)
        self.assertIn("__version__", out)

    def test_check_detects_lock_mismatch(self):
        """uv.lock 里的版本落后 -> 必须报错。"""
        with open(os.path.join(self.root, "uv.lock"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                '[[package]]\nname = "pyzk20r"\nversion = "0.8.0"\nsource = {{ editable = "." }}\n'
            )
        code, out = _run_check(self.root)
        self.assertEqual(code, 1)
        self.assertIn("uv.lock", out)

    def test_check_detects_version_behind_tag(self):
        """toml 版本低于已打 tag -> 必须报错。"""
        _write_sources(self.root, "0.8.0")
        code, out = _run_check(self.root)
        self.assertEqual(code, 1)
        self.assertIn("版本漂移", out)

    def test_bump_syncs_all_sources(self):
        """bump 要同时改 pyproject / __init__ / uv.lock 三处。"""
        proc = _run_tool(self.root, "bump", "0.9.1")
        output = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, output)
        with open(os.path.join(self.root, "pyproject.toml"), encoding="utf-8") as f:
            self.assertIn('version = "0.9.1"', f.read())
        with open(os.path.join(self.root, "src", "pyzk20r", "__init__.py"), encoding="utf-8") as f:
            self.assertIn('__version__ = "0.9.1"', f.read())
        with open(os.path.join(self.root, "uv.lock"), encoding="utf-8") as f:
            self.assertIn('version = "0.9.1"', f.read())

    def test_bump_refuses_downgrade(self):
        proc = _run_tool(self.root, "bump", "0.8.0")
        output = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("不允许降级", output)


if __name__ == "__main__":
    unittest.main()
