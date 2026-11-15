# -*- coding: utf-8 -*-
"""整合流程测试: 登记 -> 识别 -> 验证 全链路 (FakeDLL 注入) 与语法守卫 (unittest)。"""

import ast
import contextlib
import importlib.machinery
import io
import os
import sys
import types
import unittest
from unittest import mock

import support  # noqa: F401  (引导 src 到 sys.path)
from support import MERGED, TEMPLATE, FakeDLLTestCase, LEDRecordingDLL, UnsupportedLEDDLL

from pyzk20r import FingerprintDB, FingerprintSession, Scanner, _binding
from pyzk20r._binding import Param

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
# examples/industrial_login.py 契约: 先注册 -> 后使用 -> 登记绿灯/登录绿灯/失败红灯
# ---------------------------------------------------------------------------


def _load_example(name):
    """按路径加载 examples/ 脚本 (3.4 兼容, 同 test_version 的加载方式)。"""
    path = os.path.join(REPO_DIR, "examples", name)
    loader = importlib.machinery.SourceFileLoader(name, path)
    module = types.ModuleType(name)
    module.__file__ = path
    loader.exec_module(module)
    return module


class TestIndustrialLoginExample(FakeDLLTestCase):
    """工业登录示例灯效契约 (FakeDLL 记录 LED 写入序列)。

    示例改用库层的 FingerprintSession 编排灯效, 因此这里的序列包含
    白灯 (等待引导) 与绿/红 (结果反馈)。
    """

    WHITE_ON = (int(Param.LED_WHITE), 1)
    WHITE_OFF = (int(Param.LED_WHITE), 0)
    GREEN = [(int(Param.LED_GREEN), 1), (int(Param.LED_GREEN), 0)]  # 亮 1 -> 灭 0 一轮
    RED = [(int(Param.LED_RED), 1), (int(Param.LED_RED), 0)]

    @classmethod
    def setUpClass(cls):
        cls.example = _load_example("industrial_login.py")

    def _led_dev(self):
        """换入带 LED 记录的 FakeDLL 并据此构造 Scanner。"""
        self.fake_dll = LEDRecordingDLL()
        _binding.dll = self.fake_dll
        return Scanner()

    def _term(self, db, dev):
        """构造终端会话 (测试不等灯效显示时长)。"""
        session = FingerprintSession(db, dev)
        session.FLASH_SECONDS = 0
        return session

    def test_login_before_register_guided(self):
        """先注册后使用: 未登记任何指纹时提示, 不采集。"""
        buf = io.StringIO()
        dev = self._led_dev()
        db = FingerprintDB()
        session = self._term(db, dev)
        with contextlib.redirect_stdout(buf):
            self.example.finger_login(db, session)
        self.assertIn("登记", buf.getvalue())
        self.assertEqual(self.fake_dll.led_writes, self.RED)  # 未登记 -> 红灯
        session.close()
        dev.close()
        db.close()

    def test_register_then_login_green(self):
        """登记成功绿灯; 刷指纹识别成功自动登录绿灯。"""
        dev = self._led_dev()
        db = FingerprintDB()
        session = self._term(db, dev)
        with mock.patch.object(self.example, "DB_FILE", ":memory:"):
            conn = self.example.open_store()
        with mock.patch("builtins.input", side_effect=["1001", "n"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.example.register_worker(conn, db, session)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.GREEN)
        with contextlib.redirect_stdout(io.StringIO()):
            self.example.finger_login(db, session)  # 采集成功 -> identify(1, 99) 命中
        self.assertEqual(
            self.fake_dll.led_writes,
            [self.WHITE_ON, self.WHITE_OFF] + self.GREEN + [self.WHITE_ON, self.WHITE_OFF] + self.GREEN,
        )
        conn.close()
        session.close()
        dev.close()
        db.close()

    def test_login_no_match_red(self):
        """识别不到 (MatchError) -> 红灯, 提示工号+密码兜底。"""
        buf = io.StringIO()
        dev = self._led_dev()
        db = FingerprintDB()
        session = self._term(db, dev)
        db.add("1001", TEMPLATE)  # 直接入库 -> identify 可命中 (再模拟未匹配)
        self.fake_dll.db_ident_ret = -20
        with contextlib.redirect_stdout(buf):
            self.example.finger_login(db, session)
        self.assertIn("未识别", buf.getvalue())
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.RED)
        session.close()
        dev.close()
        db.close()

    def test_login_capture_timeout_red(self):
        """采集超时 (CaptureTimeoutError) -> 红灯, 不向上抛。"""
        dev = self._led_dev()
        db = FingerprintDB()
        session = self._term(db, dev)
        db.add("1001", TEMPLATE)
        self.fake_dll.acquire_fail = -8  # 持续无手指
        with mock.patch.object(self.example, "LOGIN_TIMEOUT", 0):  # 立即超时
            with contextlib.redirect_stdout(io.StringIO()):
                self.example.finger_login(db, session)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.RED)
        session.close()
        dev.close()
        db.close()

    def test_login_success_without_led_hardware(self):
        """灯效写入失败时 (不支持灯效的机型), 静默降级, 登录流程不受影响。"""
        self.fake_dll = UnsupportedLEDDLL()
        _binding.dll = self.fake_dll
        dev = Scanner()
        db = FingerprintDB()
        session = self._term(db, dev)
        db.add("1001", TEMPLATE)  # 直接入库 -> fid 1, identify 默认命中
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.example.finger_login(db, session)
        self.assertIn("自动登录", buf.getvalue())  # 登录主流程完成
        self.assertEqual(self.fake_dll.led_writes, [])  # 灯效写入失败被吞掉
        session.close()
        dev.close()
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

    def test_param_codes_match_sdk_appendix(self):
        """参数码必须与 SDK 附录 1 (PDF 6.1) 一致, 不可错位。

        背景: 曾把 101/102/103 (灯效) 与 1101/1102/1103 (字符串)、
        10001/10002 (模板格式/质量) 相互写错, 导致真机上灯效无效、
        厂商信息读成乱码、模板格式被意外切换成 ISO。
        """
        from pyzk20r import Param

        expected = {
            "IMG_WIDTH": 1,
            "IMG_HEIGHT": 2,
            "DPI": 3,
            "IMG_SIZE": 106,
            "LED_WHITE": 101,
            "LED_GREEN": 102,
            "LED_RED": 103,
            "BEEP": 104,
            "VENDOR": 1101,
            "PRODUCT_NAME": 1102,
            "SERIAL_NUMBER": 1103,
            "VID_PID": 1015,
            "FAKE_SWITCH": 2002,
            "FAKE_STATUS": 2004,
            "TEMPLATE_FORMAT": 10001,
            "TEMPLATE_QUALITY": 10002,
            "DEVICE_STATUS": 50002,
        }
        for name, code in expected.items():
            with self.subTest(param=name):
                self.assertEqual(int(getattr(Param, name)), code)

    def test_param_codes_unique(self):
        """参数码不得重复 (重复意味着两个语义共用了同一个码)。"""
        from pyzk20r import Param

        codes = [int(member) for member in Param]
        self.assertEqual(len(codes), len(set(codes)))

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

    @unittest.skipIf(sys.version_info < (3, 8), "ast feature_version requires Python 3.8+")
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
            self.assertIn("except ImportError", source, "{} 的 typing 导入未做兜底处理".format(path))
            self.assertIn("pip install typing", source, "{} 缺少 typing 安装提示".format(path))

    def test_sources_use_inline_annotations(self):
        """公共 API 使用内联参数/返回标注 + typing 构造。

        禁止: 旧式全签名注释 ``# type: (...)`` 与带引号的字符串标注
        (AST 判定: 标注节点不得是字符串常量)。
        """
        core = ["device.py", "db.py", "exceptions.py", "_binding.py", "session.py"]
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

        names = (
            "Scanner",
            "FingerprintDB",
            "Frame",
            "ZKFPMError",
            "FingerprintSession",
            "SessionResult",
        )
        for name in names:
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
