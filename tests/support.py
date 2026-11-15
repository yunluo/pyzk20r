# -*- coding: utf-8 -*-
"""测试公共设施: src 路径引导、FakeDLL 函数表与注入基类 (标准库 unittest)。"""

import ctypes
import os
import struct
import sys
import unittest

# unittest 不经过 src 布局的安装也能跑: 把仓库的 src/ 插到搜索路径最前
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import _binding  # noqa: E402

WIDTH, HEIGHT, DPI = 256, 360, 500
TEMPLATE = bytes(range(1, 65))  # 64 字节假模板
IMAGE = bytes((i % 251 for i in range(WIDTH * HEIGHT)))
MERGED = bytes(range(1, 97))  # 假登记模板


class FakeDLL:
    """实现 pyzk20r 所绑定的全部 SDK 函数, 行为可脚本化。"""

    def __init__(self):
        self.device_count = 1
        self.open_ok = True
        self.init_ret = 0
        self.opened_index = None
        self.acquire_queue = []  # 0/元组=成功, 负数=错误码
        self.set_params = {}  # code -> bytes
        self.db_adds = {}  # fid -> bytes
        self.db_params = {1: 35, 2: 55}  # 阈值
        self.db_ident_result = (1, 99)  # (fid, score)
        self.db_ident_ret = 0
        self.db_ident_user_map = {}  # fid -> score (给 verify 用)
        self.verify_scores = {}  # fid -> score
        self.match_score = 88
        self.merge_ret = 0
        self.extract_ret = 0
        self.init_calls = 0
        self.term_calls = 0
        self.device_handle = 0xDE00
        self.db_handle = 0xDB00

    # -- 库 / 设备 -----------------------------------------------------------

    def ZKFPM_Init(self):
        self.init_calls += 1
        return self.init_ret

    def ZKFPM_Terminate(self):
        self.term_calls += 1
        return 0

    def ZKFPM_GetDeviceCount(self):
        return self.device_count

    def ZKFPM_OpenDevice(self, index):
        self.opened_index = index
        return self.device_handle if self.open_ok else None

    def ZKFPM_CloseDevice(self, handle):
        return 0

    # -- 参数 -----------------------------------------------------------------

    def _param_data(self, code):
        return {
            1: struct.pack("<i", WIDTH),
            2: struct.pack("<i", HEIGHT),
            3: struct.pack("<i", DPI),
            106: struct.pack("<i", WIDTH * HEIGHT),
            101: b"ZKTeco Inc\x00" + b"\x00" * 246,
            102: b"Live20R\x00" + b"\x00" * 249,
            103: b"SN12345678\x00" + b"\x00" * 245,
            1015: struct.pack("<HH", 0x1B55, 0x0223),
            2002: struct.pack("<i", 1),
            2004: struct.pack("<i", 31),
            1102: struct.pack("<i", 0),
            1103: struct.pack("<i", 80),
            50002: struct.pack("<i", 1),
        }[code]

    def ZKFPM_GetParameters(self, handle, code, buf, pn):
        data = self.set_params.get(int(code)) or self._param_data(int(code))
        ctypes.memmove(buf, data, len(data))
        ctypes.cast(pn, ctypes.POINTER(ctypes.c_uint)).contents.value = len(data)
        return 0

    def ZKFPM_SetParameters(self, handle, code, buf, cb):
        self.set_params[int(code)] = buf.raw[:cb]
        return 0

    def ZKFPM_GetCaptureParamsEx(self, handle, pw, ph, pd):
        for ptr, value in ((pw, WIDTH), (ph, HEIGHT), (pd, DPI)):
            ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int)).contents.value = value
        return 0

    # -- 采集 -----------------------------------------------------------------

    def _pop_acquire(self):
        if not self.acquire_queue:
            return ("ok", IMAGE[: WIDTH * HEIGHT], TEMPLATE)
        item = self.acquire_queue.pop(0)
        if isinstance(item, int) and item == 0:
            return ("ok", IMAGE[: WIDTH * HEIGHT], TEMPLATE)
        return item

    def ZKFPM_AcquireFingerprint(self, handle, img_buf, img_len, tpl_buf, plen):
        item = self._pop_acquire()
        if not isinstance(item, tuple):
            return item
        _kind, image, template = item
        ctypes.memmove(img_buf, image[:img_len], min(len(image), img_len))
        ctypes.memmove(tpl_buf, template, len(template))
        ctypes.cast(plen, ctypes.POINTER(ctypes.c_uint)).contents.value = len(template)
        return 0

    def ZKFPM_AcquireFingerprintImage(self, handle, img_buf, img_len):
        ctypes.memmove(img_buf, IMAGE[:img_len], min(len(IMAGE), img_len))
        return 0

    # -- 算法库 ---------------------------------------------------------------

    def ZKFPM_DBInit(self):
        return self.db_handle

    def ZKFPM_DBFree(self, handle):
        return 0

    def ZKFPM_DBAdd(self, handle, fid, buf, cb):
        self.db_adds[fid] = buf.raw[:cb]
        return 0

    def ZKFPM_DBDel(self, handle, fid):
        self.db_adds.pop(fid, None)
        return 0

    def ZKFPM_DBClear(self, handle):
        self.db_adds.clear()
        return 0

    def ZKFPM_DBCount(self, handle, pn):
        ctypes.cast(pn, ctypes.POINTER(ctypes.c_uint)).contents.value = len(self.db_adds)
        return 0

    def ZKFPM_DBMerge(self, handle, t1, t2, t3, out, plen):
        if self.merge_ret != 0:
            return self.merge_ret
        ctypes.memmove(out, MERGED, len(MERGED))
        ctypes.cast(plen, ctypes.POINTER(ctypes.c_uint)).contents.value = len(MERGED)
        return 0

    def ZKFPM_DBIdentify(self, handle, buf, cb, pfid, pscore):
        if self.db_ident_ret != 0:
            return self.db_ident_ret
        fid, score = self.db_ident_result
        ctypes.cast(pfid, ctypes.POINTER(ctypes.c_uint)).contents.value = fid
        ctypes.cast(pscore, ctypes.POINTER(ctypes.c_uint)).contents.value = score
        return 0

    def ZKFPM_VerifyByID(self, handle, fid, buf, cb):
        return self.verify_scores.get(fid, 0)

    def ZKFPM_DBMatch(self, handle, a, cb_a, b, cb_b):
        return self.match_score

    def ZKFPM_DBSetParameter(self, handle, code, value):
        self.db_params[int(code)] = int(value)
        return 0

    def ZKFPM_DBGetParameter(self, handle, code, pvalue):
        ctypes.cast(pvalue, ctypes.POINTER(ctypes.c_int)).contents.value = self.db_params[int(code)]
        return 0

    # -- 辅助 -----------------------------------------------------------------

    def ZKFPM_ExtractFromImage(self, handle, path, dpi, out, plen):
        if self.extract_ret != 0:
            return self.extract_ret
        ctypes.memmove(out, TEMPLATE, len(TEMPLATE))
        ctypes.cast(plen, ctypes.POINTER(ctypes.c_uint)).contents.value = len(TEMPLATE)
        return 0

    def ZKFPM_GetTemplateQuality(self, handle, buf, cb):
        return 85


class FakeDLLTestCase(unittest.TestCase):
    """注入 FakeDLL 的测试基类: setUp 换入假 DLL, tearDown 还原现场。"""

    def setUp(self):
        self.fake_dll = FakeDLL()
        self._saved = (_binding.dll, _binding._init_refs)
        _binding.dll = self.fake_dll
        _binding._init_refs = 0

    def tearDown(self):
        _binding.dll, _binding._init_refs = self._saved
