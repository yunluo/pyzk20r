# -*- coding: utf-8 -*-
"""DLL 定位与加载测试 (标准库 unittest)。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from support import FakeDLLTestCase

from pyzk20r import ZKFPMError, _binding

_WIN = sys.platform == "win32"


class TestResolveDllPath(unittest.TestCase):
    """DLL 路径解析 (纯路径逻辑, 不注入 FakeDLL)。"""

    def test_resolve_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "custom.dll")
            self.assertEqual(_binding.resolve_dll_path(p), p)

    def test_resolve_env_var(self):
        env = {_binding.DLL_ENV: r"C:\custom\libzkfp.dll"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(_binding.resolve_dll_path(), r"C:\custom\libzkfp.dll")

    def test_explicit_overrides_env(self):
        env = {_binding.DLL_ENV: r"C:\from-env.dll"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(_binding.resolve_dll_path(r"C:\explicit.dll"), r"C:\explicit.dll")

    def test_resolve_prefers_system_absolute_path(self):
        """驱动装好的机器上返回 System32 绝对路径 (防裸名劫持); 否则裸名交给系统搜索。"""
        path = _binding.resolve_dll_path()
        self.assertTrue(path.endswith("libzkfp.dll"))
        system32 = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "libzkfp.dll"
        )
        if os.path.exists(system32):
            self.assertEqual(path, system32)  # 绝对路径, 不用裸名
        else:
            self.assertEqual(path, "libzkfp.dll")


@unittest.skipIf(not _WIN, "仅 Windows")
class TestLoadRealDll(unittest.TestCase):
    """加载真 DLL (只声明原型, 不调 Init), 验证加载器与原型绑定。"""

    def test_load_real_dll_sets_prototypes(self):
        try:
            dll = _binding.load_dll()
        except OSError as e:
            raise unittest.SkipTest("libzkfp.dll 不可用 (未装驱动): {}".format(e))
        for name in (
            "ZKFPM_Init",
            "ZKFPM_OpenDevice",
            "ZKFPM_AcquireFingerprint",
            "ZKFPM_DBIdentify",
        ):
            fn = getattr(dll, name)
            self.assertIsNotNone(fn.argtypes)
            self.assertIsNotNone(fn.restype)

    def test_load_dll_explicit_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                _binding.load_dll(os.path.join(tmp, "nope.dll"))


class TestLibAddref(FakeDLLTestCase):
    """全局初始化引用计数 (FakeDLL 注入)。"""

    def test_lib_addref_init_failure(self):
        """ZKFPM_Init 返回非 0/1 时 lib_addref 必须抛 ZKFPMError 且引用计数不变。"""
        self.fake_dll.init_ret = -2
        self.assertEqual(_binding._init_refs, 0)
        with self.assertRaises(ZKFPMError) as cm:
            _binding.lib_addref()
        self.assertEqual(cm.exception.code, -2)
        self.assertEqual(_binding._init_refs, 0)
