# -*- coding: utf-8 -*-
"""真机冒烟测试: 连接 Live20R 时验证 打开->参数->关闭, 无设备自动跳过。

用真机跑全流程 (登记/识别) 见 examples/ 目录脚本。
可用 `-m "not real_device"` 取消勾选。
"""
import ctypes
import sys

import pytest

from pyzk20r import _binding

pytestmark = [
    pytest.mark.real_device,
    pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows"),
]


def test_real_device_smoke():
    dll = _binding.load_dll()
    ret = dll.ZKFPM_Init()
    if ret not in (0, 1):
        pytest.skip("ZKFPM_Init 失败 (%d)" % ret)
    try:
        if dll.ZKFPM_GetDeviceCount() < 1:
            pytest.skip("未连接 Live20R 设备")
        handle = dll.ZKFPM_OpenDevice(0)
        assert handle, "打开设备失败"
        try:
            w, h, dpi = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
            ret = dll.ZKFPM_GetCaptureParamsEx(
                handle, ctypes.byref(w), ctypes.byref(h), ctypes.byref(dpi))
            assert ret == 0, "获取采集参数失败 (%d)" % ret
            assert w.value > 0 and h.value > 0, "图像尺寸异常: %dx%d" % (w.value, h.value)
            # 部分 SLK20R 固件 dpi 读数为 0, 只在非 0 时校验范围
            if dpi.value:
                assert 300 <= dpi.value <= 1500, "DPI 异常: %d" % dpi.value
        finally:
            assert dll.ZKFPM_CloseDevice(handle) == 0
    finally:
        dll.ZKFPM_Terminate()
