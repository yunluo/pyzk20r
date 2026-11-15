# -*- coding: utf-8 -*-
"""LED 灯效诊断: 待机状态下直接向设备写绿/红灯命令, 请人工观察哪一步真的亮灯。

背景: 工业登录示例在登录成功后写 LED_GREEN (10001), SDK 返回成功但用户
观察不到绿灯。本脚本绕过采集流程, 在待机状态直写灯效, 用于区分:
  A/B 待机绿灯/红灯亮   -> 固件支持灯效, 问题出在采集后的时序/覆盖
  A/B 均不亮            -> 固件未实现这两个灯效参数 (参数码存在但无效)

用法 (需 32 位 Python, 与 libzkfp.dll 位数一致):
    set PYTHONIOENCODING=utf-8
    D:\\venv34\\Scripts\\python.exe scripts\\diag_led.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pyzk20r import Scanner, ZKFPMError  # noqa: E402


def blink(dev, color, times, seconds=1.0):
    """亮 ``seconds`` 秒再熄灭, 重复 ``times`` 次; 固件拒绝该参数时报错并中断本步。"""
    try:
        for _ in range(times):
            dev.led(color)
            time.sleep(seconds)
            dev.led(color, False)
            time.sleep(0.4)
    except ZKFPMError as e:
        print("!! 固件拒绝 {} 灯参数: {}".format(color, e))
        sys.stdout.flush()
        return False
    return True


def main():
    # 注意: 厂商 DLL 退出阶段可能带崩进程, 所有输出即时 flush
    dev = Scanner()
    print("device ready:", dev.info().vid_pid)
    sys.stdout.flush()
    print("[A] 待机状态: 即将闪绿灯 3 次 (约 4 秒) -- 请盯着灯看")
    sys.stdout.flush()
    ok_a = blink(dev, "green", 3)
    print("[B] 待机状态: 即将闪红灯 3 次 (约 4 秒) -- 请盯着灯看")
    sys.stdout.flush()
    ok_b = blink(dev, "red", 3)
    print("result: green-command-ok={} red-command-ok={}".format(ok_a, ok_b))
    dev.close()
    print("done")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
