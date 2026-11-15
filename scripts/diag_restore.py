# -*- coding: utf-8 -*-
"""设备状态恢复 + 采集参数尺寸核对。

背景: diag_params.py 的 WRITE 阶段把 DPI/防假/模板格式改成了探测值,
本脚本把它们恢复为文档默认值, 并核对 GetCaptureParamsEx 与
GetParameters(1/2/106) 的图像尺寸是否一致 (缓冲区大小是否够用)。
"""

import ctypes
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import _binding  # noqa: E402

# (参数码, 恢复值, 说明)
RESTORE = [
    (3, 500, "Image DPI"),
    (2002, 0, "Anti-fake function"),
    (10001, 0, "Template format 0=ANSI"),
]


def read_int(dll, handle, code):
    buf = ctypes.create_string_buffer(4)
    n = ctypes.c_uint(4)
    ret = dll.ZKFPM_GetParameters(handle, code, buf, ctypes.byref(n))
    if ret != 0:
        return None
    return struct.unpack("<i", buf.raw[:4])[0]


def write_int(dll, handle, code, value):
    buf = ctypes.create_string_buffer(struct.pack("<i", value), 4)
    return dll.ZKFPM_SetParameters(handle, code, buf, 4)


def main():
    dll = _binding.load_dll()
    ret = dll.ZKFPM_Init()
    if ret not in (0, 1):
        print("[FAIL] ZKFPM_Init ->", ret)
        return 1
    try:
        handle = dll.ZKFPM_OpenDevice(0)
        if not handle:
            print("[FAIL] open device")
            return 1
        try:
            print("== 恢复设备状态 ==")
            for code, value, desc in RESTORE:
                before = read_int(dll, handle, code)
                w = write_int(dll, handle, code, value)
                after = read_int(dll, handle, code)
                print(
                    "  code={:<6} {:<26} before={:<8} write_ret={:<4} after={}".format(
                        code, desc, str(before), w, after
                    )
                )
            print("== 图像尺寸三方核对 ==")
            w_param = read_int(dll, handle, 1)
            h_param = read_int(dll, handle, 2)
            size_param = read_int(dll, handle, 106)
            cw, ch, cdpi = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
            r = dll.ZKFPM_GetCaptureParamsEx(handle, ctypes.byref(cw), ctypes.byref(ch), ctypes.byref(cdpi))
            print("  GetParameters(1) width      =", w_param)
            print("  GetParameters(2) height     =", h_param)
            print("  GetParameters(106) img size =", size_param)
            print("  GetCaptureParamsEx          = {}x{} @{} (ret={})".format(cw.value, ch.value, cdpi.value, r))
            print("  params w*h                 =", (w_param * h_param) if w_param and h_param else None)
            print("  capParams w*h              =", cw.value * ch.value)
            if w_param and h_param and size_param:
                print(
                    "  --> GetParameters 自洽: {} == {} ? {}".format(
                        w_param * h_param, size_param, w_param * h_param == size_param
                    )
                )
            if cw.value * ch.value != (size_param or 0):
                print(
                    "  !! 尺寸不一致: 采集缓冲区若按 GetCaptureParamsEx 分配 ({} 字节), "
                    "设备实际可能写 {} 字节 -> 缓冲区溢出风险".format(cw.value * ch.value, size_param)
                )
        finally:
            dll.ZKFPM_CloseDevice(handle)
    finally:
        dll.ZKFPM_Terminate()
    print("done")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
