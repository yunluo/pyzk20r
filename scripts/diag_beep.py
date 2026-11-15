# -*- coding: utf-8 -*-
"""蜂鸣器诊断: 写 SDK 附录 1 的蜂鸣参数码 (104), 请人工确认是否有蜂鸣声。

对照项: 101/102/103 是已实测可用的灯效码, 用来验证"写成功但无声音"
与"写入被拒"的区别。

用法 (需 32 位 Python):
    set PYTHONIOENCODING=utf-8
    D:\\venv34\\Scripts\\python.exe scripts\\diag_beep.py
"""

import ctypes
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import Scanner, _binding  # noqa: E402

# SDK 附录 1: 104 = 蜂鸣 (只写, 文档标注 LIVE20R 不支持)
BEEP_CODE = 104
# 对照: 灯效码 (实测可用)
CONTROL_CODES = [(101, "白灯"), (102, "绿灯"), (103, "红灯")]


def write_int(dll, handle, code, value):
    buf = ctypes.create_string_buffer(struct.pack("<i", value), 4)
    return dll.ZKFPM_SetParameters(handle, code, buf, 4)


def read_int(dll, handle, code):
    buf = ctypes.create_string_buffer(4)
    n = ctypes.c_uint(4)
    ret = dll.ZKFPM_GetParameters(handle, code, buf, ctypes.byref(n))
    if ret != 0:
        return None
    return struct.unpack("<i", buf.raw[:4])[0]


def main():
    dev = Scanner()
    dll = _binding.get_dll()
    handle = dev._handle
    print("device ready:", dev.info().vid_pid, "(product:", dev.info().product + ")")
    sys.stdout.flush()

    print("\n[1] 对照组: 已实测可用的灯效码 (应写入成功)")
    sys.stdout.flush()
    for code, name in CONTROL_CODES:
        ret = write_int(dll, handle, code, 1)
        write_int(dll, handle, code, 0)
        print("    code={:<5} {:<4} write_ret={}".format(code, name, ret))
        sys.stdout.flush()

    print("\n[2] 蜂鸣器: code=104, 写 1 持续 2 秒后写 0 关闭")
    print("    -- 请听是否有蜂鸣声 --")
    sys.stdout.flush()
    ret_on = write_int(dll, handle, BEEP_CODE, 1)
    print("    写 104=1 -> ret={}  ({})".format(ret_on, _explain(ret_on)))
    sys.stdout.flush()
    time.sleep(2.0)
    ret_off = write_int(dll, handle, BEEP_CODE, 0)
    print("    写 104=0 -> ret={}".format(ret_off))
    print("    读回 104  = {}".format(read_int(dll, handle, BEEP_CODE)))
    sys.stdout.flush()

    print("\n[3] 相邻码试探: 105~110 (排除固件把蜂鸣放在别的码上)")
    sys.stdout.flush()
    for code in range(105, 111):
        ret = write_int(dll, handle, code, 1)
        write_int(dll, handle, code, 0)
        print("    code={:<5} write_ret={}".format(code, ret))
        sys.stdout.flush()

    dev.close()
    print("\ndone")
    sys.stdout.flush()


def _explain(ret):
    if ret == 0:
        return "成功, 命令已下发"
    if ret == -5:
        return "无效参数: 该机型/固件不支持此参数"
    if ret == -17:
        return "操作失败"
    return "失败"


if __name__ == "__main__":
    main()
