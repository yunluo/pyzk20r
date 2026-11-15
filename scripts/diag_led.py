# -*- coding: utf-8 -*-
"""LED 灯效诊断: 待机状态下直接向设备写绿/红灯命令, 请人工观察哪一步真的亮灯。

灯效参数码 (SDK 附录 1, 只写): 101 白 / 102 绿 / 103 红。
写入后读回只写参数一般返回无效标记 (-5), 属正常现象——以肉眼观察为准。

用法 (需 32 位 Python, 与 libzkfp.dll 位数一致):
    set PYTHONIOENCODING=utf-8
    D:\\venv34\\Scripts\\python.exe scripts\\diag_led.py
"""

import ctypes
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import Scanner, ZKFPMError, _binding  # noqa: E402

# 灯效参数码 (SDK 附录 1): 101 白 / 102 绿 / 103 红, 均为只写
LED_CODES = {"white": 101, "green": 102, "red": 103}


def read_param(dll, handle, code):
    """读回参数值, 失败返回 None。"""
    buf = ctypes.create_string_buffer(4)
    n = ctypes.c_uint(4)
    ret = dll.ZKFPM_GetParameters(handle, code, buf, ctypes.byref(n))
    if ret != 0:
        return None
    return struct.unpack("<i", buf.raw[:4])[0]


def blink(dev, dll, color, times, seconds=1.0):
    """亮 ``seconds`` 秒再熄灭, 重复 ``times`` 次; 固件拒绝该参数时报错。"""
    code = LED_CODES[color]
    for i in range(times):
        try:
            dev.led(color)
            after_on = read_param(dll, dev._handle, code)
            time.sleep(seconds)
            dev.led(color, False)
            after_off = read_param(dll, dev._handle, code)
            print("    {:<6} 第{:d}次  亮后读回={}  灭后读回={}".format(color, i + 1, after_on, after_off))
            sys.stdout.flush()
        except ZKFPMError as e:
            print("!! 固件拒绝 {} 灯参数: {}".format(color, e))
            sys.stdout.flush()
            return False
        time.sleep(0.4)
    return True


def main():
    # 注意: 厂商 DLL 退出阶段可能带崩进程, 所有输出即时 flush
    dev = Scanner()
    dll = _binding.get_dll()
    print("device ready:", dev.info().vid_pid)
    sys.stdout.flush()
    for color in ("white", "green", "red"):
        print("[{}] 待机状态: 即将闪{}灯 3 次 (约 4 秒) -- 请盯着灯看".format(color, color))
        sys.stdout.flush()
        blink(dev, dll, color, 3)
    dev.close()
    print("done")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
