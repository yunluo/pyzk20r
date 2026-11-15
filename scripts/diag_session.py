# -*- coding: utf-8 -*-
"""FingerprintSession 真机验证: 白灯引导 -> 绿灯成功 / 红灯失败。

流程:
  1. 空库 -> wait_login 立即返回 not_enrolled (红灯), 不采集
  2. 登记一枚指纹 (采 3 次, 请按手指 3 次) -> 绿灯
  3. 再次 wait_login -> 请按手指 -> 绿灯 (1:N 命中)
  4. 用空模板比对照? 跳过; 直接 close 验证三色灯全部熄灭

用法 (需 32 位 Python):
    set PYTHONIOENCODING=utf-8
    D:\\venv34\\Scripts\\python.exe scripts\\diag_session.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import FingerprintDB, FingerprintSession, Scanner  # noqa: E402


def countdown(seconds, message):
    for i in range(seconds, 0, -1):
        print("    {} 秒后: {}".format(i, message))
        sys.stdout.flush()
        time.sleep(1.0)


def main():
    db = FingerprintDB()
    dev = Scanner()
    session = FingerprintSession(db, dev, on_event=lambda e, d: print("    [event] {}".format(e)))
    print("device:", dev.info().vid_pid, "| 库内模板:", db.count)
    sys.stdout.flush()

    print("\n[1] 空库调用 wait_login -> 期望 not_enrolled + 红灯")
    sys.stdout.flush()
    result = session.wait_login(timeout=3)
    print("    result:", result)
    sys.stdout.flush()

    print("\n[2] 登记一枚指纹 (采 3 次) -> 期望绿灯")
    print("    >>> 请依次按压手指 3 次 <<<")
    sys.stdout.flush()
    countdown(3, "开始采集")
    result = session.wait_enroll("TEST001", timeout=30)
    print("    result:", result)
    print("    库内模板:", db.count)
    sys.stdout.flush()

    print("\n[3] 再次 wait_login (1:N) -> 期望绿灯")
    print("    >>> 请按压手指 1 次 <<<")
    sys.stdout.flush()
    countdown(3, "开始等待")
    result = session.wait_login(timeout=30)
    print("    result:", result)
    sys.stdout.flush()

    print("\n[4] close() -> 三色灯应全部熄灭")
    sys.stdout.flush()
    session.close()
    db.close()
    dev.close()
    print("done")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
