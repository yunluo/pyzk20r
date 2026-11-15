# -*- coding: utf-8 -*-
"""参数码真机全量探测: 逐个读/写 SDK 附录 1 的参数码, 打印原始返回值。

用途: 当 SDK 文档与本库参数码表不一致时, 以真机实测为准。
读取全部参数码为只读操作; 写操作只回写读到的原值, 不改变设备状态。

用法 (32 位 Python, 与 libzkfp.dll 位数一致):
    set PYTHONIOENCODING=utf-8
    D:\\venv34\\Scripts\\python.exe scripts\\diag_params.py
"""

import ctypes
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import _binding  # noqa: E402

# SDK 附录 1 的权威参数码表 (PDF: ZKFinger Reader SDK C API, 6.1 Appendix 1)
CODES = [
    (1, "Int", "Image width"),
    (2, "Int", "Image height"),
    (3, "Int", "Image DPI"),
    (106, "Int", "Image data size"),
    (1015, "4-byte", "VID&PID"),
    (2002, "Int", "Anti-fake function"),
    (2004, "Int", "true finger (value&31==31)"),
    (1101, "String", "Vendor information"),
    (1102, "String", "Product name"),
    (1103, "String", "Device SN"),
    (10001, "Int", "Template format 0 ANSI / 1 ISO"),
    (10002, "Int", "Template quality of latest acquire"),
    (50002, "Int", "Device status 0 abnormal / 1 ok"),
    # 只写参数: 101 白灯 / 102 绿灯 / 103 红灯 / 104 蜂鸣
    (101, "Int", "WHITE light (write-only)"),
    (102, "Int", "GREEN light (write-only)"),
    (103, "Int", "RED light (write-only)"),
    (104, "Int", "BEEP (write-only)"),
]


def show(buf, n, size):
    raw = buf.raw[:n]
    text = raw.decode("mbcs", "replace").replace("\x00", " ").strip()
    int_val = None
    if len(raw) >= 4:
        int_val = struct.unpack_from("<i", raw, 0)[0]
    parts = []
    if int_val is not None:
        parts.append("int={}".format(int_val))
    if text:
        parts.append("str={!r}".format(text[:60]))
    parts.append("len={}".format(n))
    return " | ".join(parts)


def probe_read(dll, handle, code, size=256):
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_uint(size)
    ret = dll.ZKFPM_GetParameters(handle, code, buf, ctypes.byref(n))
    return ret, n.value, buf


def read_int(dll, handle, code):
    """读 4 字节 int 参数, 失败返回 None。"""
    buf = ctypes.create_string_buffer(4)
    n = ctypes.c_uint(4)
    ret = dll.ZKFPM_GetParameters(handle, code, buf, ctypes.byref(n))
    if ret != 0:
        return None
    return struct.unpack("<i", buf.raw[:4])[0]


def probe_write(dll, handle, code, value):
    buf = ctypes.create_string_buffer(struct.pack("<i", value), 4)
    return dll.ZKFPM_SetParameters(handle, code, buf, 4)


def main():
    dll = _binding.load_dll()
    ret = dll.ZKFPM_Init()
    if ret not in (0, 1):
        print("[FAIL] ZKFPM_Init ->", ret)
        return 1
    try:
        count = dll.ZKFPM_GetDeviceCount()
        print("device count:", count)
        if count < 1:
            print("[FAIL] no device")
            return 1
        handle = dll.ZKFPM_OpenDevice(0)
        if not handle:
            print("[FAIL] open device")
            return 1
        try:
            print("=" * 78)
            print("READ  (buffer 256)")
            print("=" * 78)
            for code, dtype, desc in CODES:
                r, n, buf = probe_read(dll, handle, code)
                print("  code={:<6} {:<8} ret={:<4} {}".format(code, dtype, r, show(buf, n, 256)) + ("  <- " + desc))
            print("=" * 78)
            print("WRITE (value 1, then read back, then RESTORE original)")
            print("=" * 78)
            for code, dtype, desc in CODES:
                if dtype != "Int":
                    continue
                # 写入前先读原值, 探测后回写, 不改变设备状态
                original = read_int(dll, handle, code)
                w = probe_write(dll, handle, code, 1)
                r, n, buf = probe_read(dll, handle, code)
                restored = "-"
                if original is not None and w == 0:
                    restored = str(probe_write(dll, handle, code, original))
                print(
                    "  code={:<6} write_ret={:<4} read_ret={:<4} {:<28} restore_ret={}".format(
                        code, w, r, show(buf, n, 256), restored
                    )
                    + ("  <- " + desc)
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
