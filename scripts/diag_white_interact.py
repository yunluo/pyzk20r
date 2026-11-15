# -*- coding: utf-8 -*-
"""白灯与采集的交互探测: 验证应用层写 101=1 (白灯常亮) 是否与固件冲突。

阶段:
  1. 写 101=1, 保持 3 秒 -> 请观察白灯是否常亮 (固件不采集时应无灯)
  2. 保持 101=1, 采集一次 (请按手指) -> 固件是否接管/清掉白灯?
  3. 采集后立刻读回 101 -> 看固件把白灯状态改成了什么

用法 (需 32 位 Python):
    set PYTHONIOENCODING=utf-8
    D:\\venv34\\Scripts\\python.exe scripts\\diag_white_interact.py
"""

import ctypes
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import Scanner, _binding  # noqa: E402

WHITE = 101


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
    dev = Scanner()
    dll = _binding.get_dll()
    handle = dev._handle
    print("device ready:", dev.info().vid_pid)
    sys.stdout.flush()

    print("\n[1] 写 101=1 (白灯常亮), 保持 3 秒 -- 请看白灯是否亮")
    sys.stdout.flush()
    ret = write_int(dll, handle, WHITE, 1)
    print("    write_ret={}  readback={}".format(ret, read_int(dll, handle, WHITE)))
    sys.stdout.flush()
    time.sleep(3.0)

    print("\n[2] 保持白灯=1, 现在采集一次 (请按手指, 最多 30 秒)")
    print("    -- 观察: 采集时白灯是常亮还是闪烁? 采集完是否自动熄灭? --")
    sys.stdout.flush()
    for i in range(5, 0, -1):
        print("    {} 秒后开始等待手指...".format(i))
        sys.stdout.flush()
        time.sleep(1.0)
    print("    >>> 现在请把手指放到传感器上 <<<")
    sys.stdout.flush()
    try:
        frame = dev.acquire(poll_interval=0.1, timeout=30)
        print("    采集成功: template={:d} bytes, {:d}x{:d}".format(len(frame.template), frame.width, frame.height))
    except Exception as e:
        print("    采集失败/超时:", e)
    sys.stdout.flush()
    print("    采集后立即读回 101 =", read_int(dll, handle, WHITE))
    sys.stdout.flush()
    time.sleep(1.0)
    print("    1 秒后再读回 101 =", read_int(dll, handle, WHITE))
    sys.stdout.flush()

    print("\n[3] 收尾: 写 101=0 关闭白灯")
    sys.stdout.flush()
    print("    write_ret={}".format(write_int(dll, handle, WHITE, 0)))
    dev.close()
    print("done")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
