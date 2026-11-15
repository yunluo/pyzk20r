# -*- coding: utf-8 -*-
"""整合流程测试: 登记 -> 识别 -> 验证 全链路 (FakeDLL 注入) 与 3.4 语法守卫。"""
import ast
import os
import sys

import pytest
from conftest import MERGED, TEMPLATE

from pyzk20r import FingerprintDB, Scanner

PKG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyzk20r")


# ---------------------------------------------------------------------------
# 全链路整合
# ---------------------------------------------------------------------------

def test_enroll_identify_verify_flow(fake_dll):
    """登记 2 人各 1 枚 -> 识别返回对应用户 -> 1:1 验证通过。"""
    db = FingerprintDB()
    with Scanner() as dev:
        db.enroll(dev, user_id=1001, poll_interval=0)
        assert fake_dll.db_adds[1] == MERGED        # 3 次采集合并入库
        assert db.count == 1
        db.add(1002, TEMPLATE)
        fake_dll.db_ident_result = (1, 97)
        user_id, score = db.identify(dev.acquire().template)
        assert user_id == 1001
        assert score == 97
        fake_dll.verify_scores = {1: 80}
        assert db.verify(1001, dev.acquire().template) == 80
    db.close()


def test_enroll_multiple_fingers_same_user(fake_dll):
    """同一人连续登记两枚指纹 (两次完整登记流程)。"""
    db = FingerprintDB()
    with Scanner() as dev:
        db.enroll(dev, 1001, poll_interval=0)
        db.enroll(dev, 1001, poll_interval=0)
        assert db.count == 2
        assert db.fingers_of(1001) == [MERGED, MERGED]
        assert db.users == [1001]
    db.close()


# ---------------------------------------------------------------------------
# Python 3.4 语法守卫: 包内源码必须能在 3.4 语法下解析, 且不 import typing
# ---------------------------------------------------------------------------

def _package_sources():
    for name in sorted(os.listdir(PKG_DIR)):
        if name.endswith(".py"):
            path = os.path.join(PKG_DIR, name)
            with open(path, "rb") as f:
                yield path, f.read().decode("utf-8")


@pytest.mark.skipif(sys.version_info < (3, 8), reason="ast feature_version 需 Python 3.8+")
def test_sources_parse_as_py34():
    """feature_version=(3,4) 拒绝 3.5+ 语法 (f-string/变量标注/async 等)。"""
    for path, source in _package_sources():
        ast.parse(source, filename=path, feature_version=(3, 4))


def test_sources_have_no_fstring_and_no_runtime_typing():
    """双保险: 禁 f-string; 禁运行时 typing 导入 (仅允许 ``if False:`` 死分支内的标注导入)。"""
    for path, source in _package_sources():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            assert not isinstance(node, ast.JoinedStr), "f-string 禁用于 3.4: %s" % path
        for node in _live_imports(tree):
            mod = getattr(node, "module", "") or ""
            assert mod != "typing", "禁止运行时导入 typing: %s" % path
            for alias in node.names:
                assert alias.name != "typing", "禁止运行时导入 typing: %s" % path


def _live_imports(tree):
    """返回 AST 中不在 ``if False:`` 死分支内的 Import/ImportFrom 节点。"""
    live = []

    def visit(node, dead):
        for child in ast.iter_child_nodes(node):
            child_dead = dead or (
                isinstance(child, ast.If) and _is_constant_false(child.test)
            )
            if isinstance(child, (ast.Import, ast.ImportFrom)) and not child_dead:
                live.append(child)
            visit(child, child_dead)

    visit(tree, False)
    return live


def _is_constant_false(test):
    if isinstance(test, ast.Name):
        return test.id == "False"
    return isinstance(test, ast.Constant) and test.value is False


def test_sources_use_inline_annotations():
    """公共 API 使用内联参数/返回标注; 禁止旧式全签名注释 ``# type: (...)``。

    变量类型注释 (如 ``x = None  # type: Optional[int]``) 仍允许 —— 3.4 无变量标注语法。
    """
    core = ["device.py", "db.py", "exceptions.py", "_binding.py"]
    for name in core:
        path = os.path.join(PKG_DIR, name)
        assert os.path.exists(path)
        with open(path, "rb") as f:
            src = f.read().decode("utf-8")
        assert "# type: (" not in src, "%s 仍存在旧式签名注释标注" % name
        assert "-> " in src, "%s 缺少内联返回类型标注" % name


@pytest.mark.parametrize("name", ["Scanner", "FingerprintDB", "Frame", "ZKFPMError"])
def test_public_api_exported(name):
    import pyzk20r
    assert hasattr(pyzk20r, name)
    assert name in pyzk20r.__all__
