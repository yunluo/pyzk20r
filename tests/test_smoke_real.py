# -*- coding: utf-8 -*-
"""真机冒烟测试: 连接 Live20R 时验证 打开->参数->关闭 (unittest)。

默认关闭: 厂商 DLL 的跨进程退出状态不稳定 (一个进程加载过 libzkfp.dll 后,
紧接着的测试进程可能被带崩), 设 ``PYZK20R_REAL_SMOKE=1`` 才加载真 DLL。
用真机跑全流程 (登记/识别) 见 examples/ 目录脚本。
"""

import ctypes
import os
import sys
import unittest

import support  # noqa: F401  (引导 src 到 sys.path)

from pyzk20r import _binding


@unittest.skipUnless(sys.platform == "win32", "Windows only")
@unittest.skipUnless(
    os.environ.get("PYZK20R_REAL_SMOKE") == "1", "set PYZK20R_REAL_SMOKE=1 to enable"
)
class TestRealDeviceSmoke(unittest.TestCase):
    """连接真机时验证底层链路; 无驱动/无设备自动 skip。"""

    def test_real_device_smoke(self):
        try:
            dll = _binding.load_dll()
        except OSError as e:
            raise unittest.SkipTest("libzkfp.dll unavailable (driver not installed): {}".format(e))
        ret = dll.ZKFPM_Init()
        if ret not in (0, 1):
            raise unittest.SkipTest("ZKFPM_Init failed ({:d})".format(ret))
        try:
            if dll.ZKFPM_GetDeviceCount() < 1:
                raise unittest.SkipTest("no Live20R device connected")
            handle = dll.ZKFPM_OpenDevice(0)
            self.assertTrue(handle, "failed to open device")
            try:
                w, h, dpi = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
                ret = dll.ZKFPM_GetCaptureParamsEx(handle, ctypes.byref(w), ctypes.byref(h), ctypes.byref(dpi))
                self.assertEqual(ret, 0, "failed to get capture params ({:d})".format(ret))
                self.assertTrue(
                    w.value > 0 and h.value > 0,
                    "abnormal image size: {:d}x{:d}".format(w.value, h.value),
                )
                # 部分 SLK20R 固件 dpi 读数为 0, 只在非 0 时校验范围
                if dpi.value:
                    self.assertTrue(300 <= dpi.value <= 1500, "abnormal DPI: {:d}".format(dpi.value))
            finally:
                self.assertEqual(dll.ZKFPM_CloseDevice(handle), 0)
        finally:
            dll.ZKFPM_Terminate()


if __name__ == "__main__":
    unittest.main()
