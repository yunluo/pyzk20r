# -*- coding: utf-8 -*-
"""DLL 定位与加载测试。"""
import os
import struct

import pytest

from pyzk20r import ZKFPMError, _binding


def test_resolve_explicit_path(tmp_path):
    p = str(tmp_path / "custom.dll")
    assert _binding.resolve_dll_path(p) == p


def test_resolve_env_var(monkeypatch):
    monkeypatch.setenv(_binding.DLL_ENV, r"C:\custom\libzkfp.dll")
    assert _binding.resolve_dll_path() == r"C:\custom\libzkfp.dll"


def test_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv(_binding.DLL_ENV, r"C:\from-env.dll")
    assert _binding.resolve_dll_path(r"C:\explicit.dll") == r"C:\explicit.dll"


def test_resolve_prefers_system_over_bundled():
    """驱动装好的机器上应返回 System32 绝对路径 (防裸名劫持); 无系统副本才回退包内。"""
    path = _binding.resolve_dll_path()
    assert path.endswith("libzkfp.dll")
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "libzkfp.dll")
    if os.path.exists(system32):
        assert path == system32                       # 绝对路径, 不用裸名
    else:
        assert os.path.sep in path and "dlls" in path


def test_bundled_dll_arch_matches_python():
    """包内捆绑的 DLL 必须存在, 且与当前 Python 位数一致 (回退场景用)。"""
    arch = "x64" if struct.calcsize("P") == 8 else "x86"
    path = os.path.join(os.path.dirname(_binding.__file__), "dlls", arch, "libzkfp.dll")
    assert os.path.exists(path)


def test_load_real_dll_sets_prototypes():
    """加载真 DLL (只声明原型, 不调 Init), 验证加载器与原型绑定。"""
    dll = _binding.load_dll()
    for name in ("ZKFPM_Init", "ZKFPM_OpenDevice", "ZKFPM_AcquireFingerprint", "ZKFPM_DBIdentify"):
        fn = getattr(dll, name)
        assert fn.argtypes is not None
        assert fn.restype is not None


def test_load_dll_explicit_missing(tmp_path):
    with pytest.raises(OSError):
        _binding.load_dll(str(tmp_path / "nope.dll"))


def test_lib_addref_init_failure(fake_dll):
    """ZKFPM_Init 返回非 0/1 时 lib_addref 必须抛 ZKFPMError 且引用计数不变。"""
    fake_dll.init_ret = -2
    assert _binding._init_refs == 0
    with pytest.raises(ZKFPMError) as ei:
        _binding.lib_addref()
    assert ei.value.code == -2
    assert _binding._init_refs == 0
