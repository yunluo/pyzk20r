# -*- coding: utf-8 -*-
"""DLL 定位与加载测试。"""

import os
import sys

import pytest

from pyzk20r import ZKFPMError, _binding

_WIN = sys.platform == "win32"


def test_resolve_explicit_path(tmp_path):
    p = str(tmp_path / "custom.dll")
    assert _binding.resolve_dll_path(p) == p


def test_resolve_env_var(monkeypatch):
    monkeypatch.setenv(_binding.DLL_ENV, r"C:\custom\libzkfp.dll")
    assert _binding.resolve_dll_path() == r"C:\custom\libzkfp.dll"


def test_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv(_binding.DLL_ENV, r"C:\from-env.dll")
    assert _binding.resolve_dll_path(r"C:\explicit.dll") == r"C:\explicit.dll"


def test_resolve_prefers_system_absolute_path():
    """驱动装好的机器上返回 System32 绝对路径 (防裸名劫持); 否则裸名交给系统搜索。"""
    path = _binding.resolve_dll_path()
    assert path.endswith("libzkfp.dll")
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "libzkfp.dll")
    if os.path.exists(system32):
        assert path == system32  # 绝对路径, 不用裸名
    else:
        assert path == "libzkfp.dll"


@pytest.mark.skipif(not _WIN, reason="仅 Windows")
def test_load_real_dll_sets_prototypes():
    """加载真 DLL (只声明原型, 不调 Init), 验证加载器与原型绑定。"""
    try:
        dll = _binding.load_dll()
    except OSError as e:
        pytest.skip("libzkfp.dll 不可用 (未装驱动): {}".format(e))
    for name in ("ZKFPM_Init", "ZKFPM_OpenDevice", "ZKFPM_AcquireFingerprint", "ZKFPM_DBIdentify"):
        fn = getattr(dll, name)
        assert fn.argtypes is not None
        assert fn.restype is not None


@pytest.mark.skipif(not _WIN, reason="仅 Windows (依赖 ctypes.WinDLL)")
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
