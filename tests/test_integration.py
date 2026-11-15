# -*- coding: utf-8 -*-
"""整合流程测试: 登记 -> 识别 -> 验证 全链路 (FakeDLL 注入) 与语法守卫。"""
import ast
import os
import sys

import pytest
from conftest import MERGED, TEMPLATE

from pyzk20r import FingerprintDB, Scanner

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(REPO_DIR, "src", "pyzk20r")


# ---------------------------------------------------------------------------
# 全链路整合
# ---------------------------------------------------------------------------

def test_enroll_identify_verify_flow(fake_dll):
    """登记 2 人各 1 枚 -> 识别返回对应用户 -> 1:1 验证通过。"""
    db = FingerprintDB()
    with Scanner() as dev:
        db.enroll(dev, user_id="1001", poll_interval=0)
        assert fake_dll.db_adds[1] == MERGED        # 3 次采集合并入库
        assert db.count == 1
        db.add("1002", TEMPLATE)
        fake_dll.db_ident_result = (1, 97)
        user_id, score = db.identify(dev.acquire().template)
        assert user_id == "1001"
        assert score == 97
        fake_dll.verify_scores = {1: 80}
        assert db.verify("1001", dev.acquire().template) == 80
    db.close()


def test_enroll_multiple_fingers_same_user(fake_dll):
    """同一人连续登记两枚指纹 (两次完整登记流程)。"""
    db = FingerprintDB()
    with Scanner() as dev:
        db.enroll(dev, "1001", poll_interval=0)
        db.enroll(dev, "1001", poll_interval=0)
        assert db.count == 2
        assert db.fingers_of("1001") == [MERGED, MERGED]
        assert db.users == ["1001"]
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
def test_sources_parse_as_py37():
    """feature_version=(3,7) 拒绝 3.8+ 语法 (海象运算符/仅位置参数/赋值表达式 等)。"""
    for path, source in _package_sources():
        ast.parse(source, filename=path, feature_version=(3, 7))


def test_typing_import_guarded():
    """typing 导入必须用 try/except 包裹, 缺失时给出明确提示。"""
    for path, source in _package_sources():
        if "from typing import" not in source:
            continue
        assert "except ImportError" in source, "%s 的 typing 导入未做兜底处理" % path
        assert "pip install typing" in source, "%s 缺少 typing 安装提示" % path


def test_sources_use_inline_annotations():
    """公共 API 使用内联参数/返回标注 + typing 构造。

    禁止: 旧式全签名注释 ``# type: (...)`` 与带引号的字符串标注
    (AST 判定: 标注节点不得是字符串常量)。
    """
    core = ["device.py", "db.py", "exceptions.py", "_binding.py"]
    for name in core:
        path = os.path.join(PKG_DIR, name)
        assert os.path.exists(path)
        with open(path, "rb") as f:
            src = f.read().decode("utf-8")
        assert "# type: (" not in src, "%s 仍存在旧式签名注释标注" % name
        assert "-> " in src, "%s 缺少内联返回类型标注" % name
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            annotations = [node.returns]
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                annotations.append(arg.annotation)
            for ann in annotations:
                if ann is None:
                    continue
                assert not (isinstance(ann, ast.Constant) and isinstance(ann.value, str)), (
                    "%s 存在带引号的字符串标注: %s" % (name, ast.dump(ann)))


@pytest.mark.parametrize("name", ["Scanner", "FingerprintDB", "Frame", "ZKFPMError"])
def test_public_api_exported(name):
    import pyzk20r
    assert hasattr(pyzk20r, name)
    assert name in pyzk20r.__all__


def test_version_consistent_with_pyproject():
    """__init__.__version__ 与 pyproject.toml 的 version 必须一致 (防双处漂移)。"""
    import re

    import pyzk20r
    pyproject = os.path.join(REPO_DIR, "pyproject.toml")
    with open(pyproject, "rb") as f:
        text = f.read().decode("utf-8")
    match = re.search(r'(?m)^version = "(.+)"', text)
    assert match, "pyproject.toml 缺少 version"
    assert pyzk20r.__version__ == match.group(1)
