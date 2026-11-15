# -*- coding: utf-8 -*-
"""整合流程测试: 登记 -> 识别 -> 验证 全链路 (FakeDLL 注入) 与语法守卫 (unittest)。"""

import ast
import os
import sys
import unittest

import support  # noqa: F401  (引导 src 到 sys.path)
from support import MERGED, TEMPLATE, FakeDLLTestCase

from pyzk20r import FingerprintDB, Scanner

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(REPO_DIR, "src", "pyzk20r")

if hasattr(ast, "Constant"):  # Python 3.6+
    _CONSTANT_TYPES = (ast.Constant,)

    def _const_value(node):
        return getattr(node, "value", None)
else:  # Python 3.4/3.5: 旧节点类型 (3.12 起已移除, 不影响现代版本)
    _CONSTANT_TYPES = (ast.Str, ast.Num)

    def _const_value(node):
        return getattr(node, "s", getattr(node, "n", None))


# ---------------------------------------------------------------------------
# 全链路整合
# ---------------------------------------------------------------------------


class TestEnrollIdentifyFlow(FakeDLLTestCase):
    """登记 -> 识别 -> 验证 全链路 (FakeDLL 注入)。"""

    def test_enroll_identify_verify_flow(self):
        """登记 2 人各 1 枚 -> 识别返回对应用户 -> 1:1 验证通过。"""
        db = FingerprintDB()
        with Scanner() as dev:
            db.enroll(dev, user_id="1001", poll_interval=0)
            self.assertEqual(self.fake_dll.db_adds[1], MERGED)  # 3 次采集合并入库
            self.assertEqual(db.count, 1)
            db.add("1002", TEMPLATE)
            self.fake_dll.db_ident_result = (1, 97)
            user_id, score = db.identify(dev.acquire().template)
            self.assertEqual(user_id, "1001")
            self.assertEqual(score, 97)
            self.fake_dll.verify_scores = {1: 80}
            self.assertEqual(db.verify("1001", dev.acquire().template), 80)
        db.close()

    def test_enroll_multiple_fingers_same_user(self):
        """同一人连续登记两枚指纹 (两次完整登记流程)。"""
        db = FingerprintDB()
        with Scanner() as dev:
            db.enroll(dev, "1001", poll_interval=0)
            db.enroll(dev, "1001", poll_interval=0)
            self.assertEqual(db.count, 2)
            self.assertEqual(db.fingers_of("1001"), [MERGED, MERGED])
            self.assertEqual(db.users, ["1001"])
        db.close()


# ---------------------------------------------------------------------------
# Python 3.4 语法守卫: 包内源码必须能在 3.4 语法下解析
# ---------------------------------------------------------------------------


def _package_sources():
    for name in sorted(os.listdir(PKG_DIR)):
        if name.endswith(".py"):
            path = os.path.join(PKG_DIR, name)
            with open(path, "rb") as f:
                yield path, f.read().decode("utf-8")


def _repo_python_sources():
    """库源码 + 测试 + 示例 全部 .py 文件。"""
    for sub in ("src", "tests", "examples"):
        root = os.path.join(REPO_DIR, sub)
        for dirpath, _dirs, files in os.walk(root):
            for name in sorted(files):
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    with open(path, "rb") as f:
                        yield path, f.read().decode("utf-8")


class TestSyntaxGuards(unittest.TestCase):
    """源码风格与语法下限守卫。"""

    def test_no_percent_string_formatting(self):
        """禁止 % 风格字符串格式化, 一律使用 str.format()。

        取模运算 (数值 % 整数) 不受影响, 只拦截 "字符串" % 参数 的形式。
        """
        for path, source in _repo_python_sources():
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)):
                    continue
                value = _const_value(node.left) if isinstance(node.left, _CONSTANT_TYPES) else None
                self.assertFalse(
                    isinstance(value, str),
                    "{} 仍使用 % 格式化, 请改用 .format(): {}".format(path, value),
                )

    @unittest.skipIf(sys.version_info < (3, 8), "ast feature_version 需 Python 3.8+")
    def test_sources_parse_as_py34(self):
        """feature_version=(3,4) 拒绝 3.5+ 语法 (f-string/变量标注/async/海象运算符 等)。

        运行时兼容声明为 3.4 (XP 部署), 语法必须守住下限。
        """
        for path, source in _package_sources():
            ast.parse(source, filename=path, feature_version=(3, 4))

    def test_typing_import_guarded(self):
        """typing 导入必须用 try/except 包裹, 缺失时给出明确提示。"""
        for path, source in _package_sources():
            if "from typing import" not in source:
                continue
            self.assertIn(
                "except ImportError", source, "{} 的 typing 导入未做兜底处理".format(path)
            )
            self.assertIn("pip install typing", source, "{} 缺少 typing 安装提示".format(path))

    def test_sources_use_inline_annotations(self):
        """公共 API 使用内联参数/返回标注 + typing 构造。

        禁止: 旧式全签名注释 ``# type: (...)`` 与带引号的字符串标注
        (AST 判定: 标注节点不得是字符串常量)。
        """
        core = ["device.py", "db.py", "exceptions.py", "_binding.py"]
        for name in core:
            path = os.path.join(PKG_DIR, name)
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as f:
                src = f.read().decode("utf-8")
            self.assertNotIn("# type: (", src, "{} 仍存在旧式签名注释标注".format(name))
            self.assertIn("-> ", src, "{} 缺少内联返回类型标注".format(name))
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
                    self.assertFalse(
                        isinstance(ann, _CONSTANT_TYPES) and isinstance(_const_value(ann), str),
                        "{} 存在带引号的字符串标注: {}".format(name, ast.dump(ann)),
                    )

    def test_public_api_exported(self):
        import pyzk20r

        for name in ("Scanner", "FingerprintDB", "Frame", "ZKFPMError"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(pyzk20r, name))
                self.assertIn(name, pyzk20r.__all__)

    def test_version_consistent_with_pyproject(self):
        """__init__.__version__ 与 pyproject.toml 的 version 必须一致 (防双处漂移)。"""
        import re

        import pyzk20r

        pyproject = os.path.join(REPO_DIR, "pyproject.toml")
        with open(pyproject, "rb") as f:
            text = f.read().decode("utf-8")
        match = re.search(r'(?m)^version = "(.+)"', text)
        self.assertIsNotNone(match, "pyproject.toml 缺少 version")
        self.assertEqual(pyzk20r.__version__, match.group(1))


if __name__ == "__main__":
    unittest.main()
